#!/usr/bin/env python3
"""
voice_anonymizer.py
====================

Alteratore vocale in tempo reale, pensato per anonimizzare l'impronta
vocale durante riunioni Microsoft Teams (o qualsiasi altra app) che
vengono registrate/trascritte.

Funziona su Windows e Ubuntu/Linux. Non si collega direttamente a
Teams: legge l'audio dal tuo microfono reale, lo altera e lo scrive
su un MICROFONO VIRTUALE, che poi selezioni come input in Teams.
Vedi le istruzioni di setup nel README.

Pipeline di anonimizzazione (quando attiva):

  Microfono reale
      |
      v
  [Stadio 1 — WORLD vocoder]   (pyworld, se disponibile)
    - Decomposizione F0 / inviluppo spettrale / aperiodicita'
    - Pitch shift moderato
    - Warp dei formanti (vocal tract scaling)
    - Tilt spettrale leggero (opzionale)
    - Micro time-stretch per-sessione (opzionale)
    - Randomizzazione per-sessione: ogni avvio usa parametri
      leggermente diversi, cosi' le registrazioni non condividono
      la stessa voce trasformata
      |
      v
  [Stadio 2 — Pedalboard]
    - HighpassFilter(150 Hz)
    - LowpassFilter(6000 Hz)
    - Compressor  (consistenza di livello)
    - Chorus leggero (variazioni timbriche)
    - Gain(-1 dB)
    Nota: PitchShift e' rimosso perche' gia' gestito da WORLD.
      |
      v
  Microfono virtuale (VB-CABLE / null-sink)

Se pyworld non e' installato, lo script cade automaticamente in
modalita' legacy (solo Pedalboard con PitchShift), con un avviso.

Dipendenze Python:
    pip install pedalboard sounddevice numpy pyworld
    (tkinter e' incluso nella maggior parte delle installazioni Python;
     su Ubuntu potrebbe servire: sudo apt install python3-tk)

Due modalita' d'uso:

1) INTERFACCIA GRAFICA (consigliata)
    python voice_anonymizer.py
    oppure
    python voice_anonymizer.py --gui

2) RIGA DI COMANDO
    python voice_anonymizer.py --list-devices
    python voice_anonymizer.py --input "Jabra" --output "CABLE Input"

    Durante l'esecuzione da CLI puoi digitare:
        t  + invio   -> attiva/disattiva l'alterazione
        q  + invio   -> esce dal programma

Diagnostica dispositivi:
    python voice_anonymizer.py --diagnose
"""

import argparse
import os
import random
import sys
import threading
import time
import json

import numpy as np
import sounddevice as sd

from pedalboard import (
    Pedalboard,
    PitchShift,
    HighpassFilter,
    LowpassFilter,
    Compressor,
    Chorus,
    Gain,
    Reverb,
)

# Prova a importare pyworld; se non disponibile usa la modalita' legacy
try:
    import pyworld as pw
    _PYWORLD_AVAILABLE = True
except ImportError:
    _PYWORLD_AVAILABLE = False
    print(
        "[AVVISO] pyworld non trovato: lo stadio WORLD e' disabilitato.\n"
        "         Installa con:  pip install pyworld\n"
        "         Lo script usa la modalita' legacy (solo Pedalboard).\n",
        file=sys.stderr,
    )

# Soglia RMS sotto la quale un blocco e' considerato silenzio e viene
# passato direttamente a Pedalboard senza elaborazione WORLD.
# Evita che pw.dio / cheaptrick esplodano su segnali near-zero.
_SILENCE_RMS_THRESHOLD = 1e-4

# Numero minimo di campioni richiesti da pw.dio (legato al frame_period
# di default di 5 ms). A 48 kHz, 5 ms = 240 campioni; usiamo un margine
# generoso di 3x per robustezza.
_WORLD_MIN_SAMPLES = 720


# =============================================================================
# CONFIGURAZIONE DEVICES
# =============================================================================
CONFIG_FILE = "voice_anonymizer_config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_config(in_dev, out_dev, semitones, formant, chorus, reverb, gain, world_enabled, hpf, lpf, comp_thresh, comp_ratio):
    try:
        data = {
            "input_device": in_dev,
            "output_device": out_dev,
            "semitones": semitones,
            "formant_scale": formant,
            "chorus_mix": chorus,
            "reverb_mix": reverb,
            "gain_db": gain,
            "world_enabled": world_enabled,
            "hpf_cutoff": hpf,
            "lpf_cutoff": lpf,
            "comp_threshold": comp_thresh,
            "comp_ratio": comp_ratio
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Errore salvataggio config: {e}", file=sys.stderr)

# =============================================================================
# CONFIGURAZIONE PRIVACY VOCALE
# =============================================================================

class VoicePrivacyConfig:
    """
    Parametri per lo stadio WORLD.

    Se randomize=True, all'avvio viene generata una variante casuale
    all'interno delle finestre di variazione specificate, cosi' ogni
    sessione produce una voce trasformata leggermente diversa (piu'
    difficile da confrontare tra registrazioni).
    """

    def __init__(
        self,
        enabled: bool = True,
        # Pitch shift base in semitoni (negativo = piu' grave)
        pitch_shift: float = -2.5,
        # Warp dei formanti: 1.0 = nessun cambiamento,
        #   >1.0 = tratto vocale piu' corto (voce piu' acuta/infantile),
        #   <1.0 = tratto vocale piu' lungo (voce piu' grave/adulta)
        formant_scale: float = 1.10,
        # Randomizzazione per-sessione
        randomize: bool = True,
        pitch_variation: float = 0.3,      # semitoni +/-
        formant_variation: float = 0.03,   # scala +/-
        # Tilt spettrale in dB/octave (+1 = piu' luminoso, -1 = piu' scuro)
        spectral_tilt: float = 0.0,
        spectral_tilt_variation: float = 1.0,
        # Micro time-stretch (1.0 = nessun cambiamento; es. 0.98 o 1.03)
        time_scale: float = 1.0,
        time_scale_variation: float = 0.0,
        # Noise floor in dBFS per disturbare feature spettrali stabili
        # None = disabilitato
        noise_floor_dbfs: float | None = -55.0,
    ):
        self.enabled = enabled
        self.pitch_shift = pitch_shift
        self.formant_scale = formant_scale
        self.randomize = randomize
        self.pitch_variation = pitch_variation
        self.formant_variation = formant_variation
        self.spectral_tilt = spectral_tilt
        self.spectral_tilt_variation = spectral_tilt_variation
        self.time_scale = time_scale
        self.time_scale_variation = time_scale_variation
        self.noise_floor_dbfs = noise_floor_dbfs

    def resolve(self) -> "VoicePrivacyConfig":
        """
        Ritorna una copia con i parametri effettivi per questa sessione,
        applicando la randomizzazione se abilitata.
        """
        cfg = VoicePrivacyConfig(
            enabled=self.enabled,
            pitch_shift=self.pitch_shift,
            formant_scale=self.formant_scale,
            randomize=False,
            spectral_tilt=self.spectral_tilt,
            time_scale=self.time_scale,
            noise_floor_dbfs=self.noise_floor_dbfs,
        )
        if self.randomize:
            cfg.pitch_shift += random.uniform(
                -self.pitch_variation, self.pitch_variation
            )
            cfg.formant_scale += random.uniform(
                -self.formant_variation, self.formant_variation
            )
            cfg.spectral_tilt += random.uniform(
                -self.spectral_tilt_variation, self.spectral_tilt_variation
            )
            if self.time_scale_variation > 0:
                cfg.time_scale += random.uniform(
                    -self.time_scale_variation, self.time_scale_variation
                )
        return cfg


# =============================================================================
# STADIO WORLD (pyworld)
# =============================================================================

def _semitones_to_ratio(semitones: float) -> float:
    return 2.0 ** (semitones / 12.0)


def _apply_spectral_tilt(sp: np.ndarray, tilt_db_per_oct: float, sr: int) -> np.ndarray:
    """
    Applica un tilt spettrale lineare (in dB/ottava) all'inviluppo spettrale.
    Positivo = enfatizza le alte frequenze; negativo = enfatizza le basse.
    """
    if tilt_db_per_oct == 0.0:
        return sp
    n_bins = sp.shape[1]
    freqs = np.linspace(0, sr / 2, n_bins)
    freqs[0] = 1.0  # evita log2(0)
    ref_freq = 1000.0
    gains = 10.0 ** (tilt_db_per_oct * np.log2(freqs / ref_freq) / 20.0)
    # Clip gains per evitare amplificazioni/attenuazioni estreme
    gains = np.clip(gains, 0.1, 10.0)
    return sp * gains[np.newaxis, :]


def _sanitize_spectrum(sp: np.ndarray) -> np.ndarray:
    """
    Sostituisce NaN/Inf nell'inviluppo spettrale con valori minimi sicuri.
    WORLD a volte produce NaN su frame di silenzio o segnali molto brevi.
    """
    if not np.isfinite(sp).all():
        sp = np.where(np.isfinite(sp), sp, 1e-16)
    # L'inviluppo deve essere strettamente positivo (cheaptrick lo garantisce
    # normalmente, ma dopo il warp dei formanti potremmo avere zeri esatti)
    return np.maximum(sp, 1e-16)


def world_process(
    audio: np.ndarray,
    sr: int,
    cfg: "VoicePrivacyConfig",
) -> np.ndarray:
    """
    Applica lo stadio WORLD al blocco audio mono float32.

    Garanzie di stabilita':
    - Se il blocco e' troppo corto o in silenzio, viene restituito invariato.
    - NaN/Inf nell'inviluppo spettrale vengono sanificati prima della risintesi.
    - L'output e' sempre riportato alla lunghezza esatta dell'ingresso.
    - Il segnale in uscita e' soft-clippato a ±1.0 prima di essere restituito.

    Passi:
    1. Decomposizione F0 / spettro / aperiodicita'
    2. Pitch shift (modifica F0)
    3. Warp formanti (vocal tract length scaling dell'inviluppo)
    4. Tilt spettrale
    5. Sanificazione NaN/Inf
    6. Risintesi
    7. Micro time-stretch (ricampionamento leggero)
    8. Restituzione alla lunghezza originale (pad/trim)
    9. Noise floor (iniezione rumore sagomato)
    10. Soft clip a ±1.0
    """
    orig_len = len(audio)

    # --- Guardia silenzio / blocco troppo corto ---
    # Entrambe le condizioni farebbero comportare male pw.dio / cheaptrick.
    if orig_len < _WORLD_MIN_SAMPLES:
        return audio
    rms = float(np.sqrt(np.mean(audio ** 2)))
    if rms < _SILENCE_RMS_THRESHOLD:
        return audio

    # pyworld opera su float64 mono
    x = audio.astype(np.float64)

    # --- Decomposizione ---
    _f0, t = pw.dio(x, sr)
    f0 = pw.stonemask(x, _f0, t, sr)
    sp = pw.cheaptrick(x, f0, t, sr)
    ap = pw.d4c(x, f0, t, sr)

    # --- Sanificazione post-decomposizione ---
    # Se cheaptrick o d4c restituiscono NaN/Inf (es. su frame quasi-silenziosi
    # nel mezzo del blocco), li sostituiamo prima di modificare e risintetizzare.
    sp = _sanitize_spectrum(sp)
    ap = np.clip(ap, 0.0, 1.0)          # aperiodicita' deve stare in [0,1]

    # --- Pitch shift: scala F0 dove e' voicata ---
    pitch_ratio = _semitones_to_ratio(cfg.pitch_shift)
    f0_shifted = np.where(f0 > 0, f0 * pitch_ratio, 0.0)

    # --- Warp formanti (vocal tract length scaling) ---
    # Per ogni bin di destinazione, calcola il bin sorgente e interpola.
    n_bins = sp.shape[1]
    scale = max(cfg.formant_scale, 0.5)   # evita scale degeneri
    src_idx = np.clip(
        np.arange(n_bins, dtype=np.float64) / scale, 0.0, n_bins - 1.0
    )
    bin_axis = np.arange(n_bins, dtype=np.float64)
    sp_warped = np.empty_like(sp)
    for i in range(sp.shape[0]):
        sp_warped[i] = np.interp(src_idx, bin_axis, sp[i])

    # --- Tilt spettrale ---
    sp_tilted = _apply_spectral_tilt(sp_warped, cfg.spectral_tilt, sr)

    # --- Sanificazione post-trasformazione ---
    sp_tilted = _sanitize_spectrum(sp_tilted)

    # --- Risintesi ---
    synthesized = pw.synthesize(f0_shifted, sp_tilted, ap, sr)
    synthesized = synthesized.astype(np.float32)

    # --- Micro time-stretch ---
    # Ricampionamento lineare; il risultato viene sempre riportato
    # alla lunghezza orig_len per non rompere il buffer di sounddevice.
    if abs(cfg.time_scale - 1.0) > 1e-4 and len(synthesized) > 0:
        stretched_len = max(1, int(round(len(synthesized) * cfg.time_scale)))
        x_src = np.linspace(0.0, 1.0, len(synthesized))
        x_dst = np.linspace(0.0, 1.0, stretched_len)
        synthesized = np.interp(x_dst, x_src, synthesized).astype(np.float32)

    # --- Restituzione alla lunghezza esatta dell'ingresso ---
    # pw.synthesize puo' produrre un output leggermente piu' corto/lungo
    # (dipende dall'allineamento dei frame interni di WORLD); lo correggiamo
    # sempre, indipendentemente dal time-stretch.
    if len(synthesized) >= orig_len:
        synthesized = synthesized[:orig_len]
    else:
        synthesized = np.pad(synthesized, (0, orig_len - len(synthesized)))

    # --- Noise floor ---
    if cfg.noise_floor_dbfs is not None:
        noise_amp = float(10.0 ** (cfg.noise_floor_dbfs / 20.0))
        noise = (np.random.randn(orig_len) * noise_amp).astype(np.float32)
        synthesized = synthesized + noise

    # --- Soft clip a ±1.0 ---
    # La risintesi WORLD puo' amplificare il segnale; clippiamo con tanh
    # per evitare overflow nel downstream Pedalboard / nel buffer sounddevice.
    synthesized = np.tanh(synthesized)

    return synthesized


# =============================================================================
# LOGICA DI ALTERAZIONE VOCALE (condivisa tra CLI e GUI)
# =============================================================================

class VoiceAnonymizer:
    """
    Incapsula la catena di effetti e lo stato attivo/disattivo.

    Stadio 1 (WORLD): se pyworld e' disponibile e privacy_cfg.enabled e'
    True, viene applicato il vocoder per modificare F0, formanti e tilt.

    Stadio 2 (Pedalboard): catena di effetti DSP (HPF, LPF, compressore,
    chorus, gain). PitchShift e' incluso solo in modalita' legacy
    (pyworld non disponibile).
    """

    def __init__(
        self,
        semitones: float = -4.0,
        chorus_mix: float = 0.03,
        reverb_mix: float = 0.15,
        gain_db: float = 0.0,
        world_enabled: bool = True,
        hpf_cutoff: int = 150,
        lpf_cutoff: int = 6000,
        comp_threshold: float = -22.0,
        comp_ratio: float = 3.0,
        enabled: bool = True,
        privacy_cfg: VoicePrivacyConfig | None = None,
    ):
        self.semitones = semitones
        self.chorus_mix = chorus_mix
        self.reverb_mix = reverb_mix
        self.gain_db = gain_db
        self.world_enabled = world_enabled
        self.hpf_cutoff = hpf_cutoff
        self.lpf_cutoff = lpf_cutoff
        self.comp_threshold = comp_threshold
        self.comp_ratio = comp_ratio
        self.enabled = enabled
        self._lock = threading.Lock()

        self._privacy_cfg_template = privacy_cfg or VoicePrivacyConfig()
        self._session_cfg = self._privacy_cfg_template.resolve()
        self._session_cfg.enabled = world_enabled

        self._build_boards()

    def _build_boards(self):
        use_world = _PYWORLD_AVAILABLE and self.world_enabled and self._session_cfg.enabled

        if use_world:
            # Pipeline avanzata con Vocoder WORLD
            self.board_on = Pedalboard([
                HighpassFilter(cutoff_frequency_hz=self.hpf_cutoff),
                LowpassFilter(cutoff_frequency_hz=self.lpf_cutoff),
                Compressor(
                    threshold_db=self.comp_threshold,
                    ratio=self.comp_ratio,
                    attack_ms=5.0,
                    release_ms=80.0,
                ),
                Chorus(
                    rate_hz=0.5,
                    depth=0.1,
                    mix=self.chorus_mix,
                ),
                Reverb(
                    room_size=0.15,
                    damping=0.7,
                    wet_level=self.reverb_mix,
                    dry_level=max(0.0, 1.0 - self.reverb_mix),
                ),
                Gain(gain_db=self.gain_db),
            ])
        else:
            # Pipeline standard Pedalboard (usata se WORLD è disattivato)
            self.board_on = Pedalboard([
                PitchShift(semitones=self.semitones),  # Ora incluso correttamente!
                HighpassFilter(cutoff_frequency_hz=self.hpf_cutoff),
                LowpassFilter(cutoff_frequency_hz=self.lpf_cutoff),
                Compressor(
                    threshold_db=self.comp_threshold,
                    ratio=self.comp_ratio,
                    attack_ms=5.0,
                    release_ms=80.0,
                ),
                Chorus(
                    rate_hz=0.5,
                    depth=0.1,
                    mix=self.chorus_mix,
                ),
                Reverb(
                    room_size=0.15,
                    damping=0.7,
                    wet_level=self.reverb_mix,
                    dry_level=max(0.0, 1.0 - self.reverb_mix),
                ),
                Gain(gain_db=self.gain_db),
            ])

        self.board_off = Pedalboard([])

    def set_world_enabled(self, value: bool):
    with self._lock:
        self.world_enabled = value
        self._session_cfg.enabled = value

        # quando passo a modalità legacy,
        # sincronizzo il pitch corrente nel PitchShift
        if not value:
            self._build_boards()

    def update_dsp_settings(self, hpf, lpf, comp_t, comp_r):
        with self._lock:
            self.hpf_cutoff = hpf
            self.lpf_cutoff = lpf
            self.comp_threshold = comp_t
            self.comp_ratio = comp_r
            self._build_boards()

    def set_chorus(self, value: float):
        with self._lock:
            self.chorus_mix = value
            for fx in self.board_on:
                if isinstance(fx, Chorus):
                    fx.mix = value
                    return
            self._build_boards()

    def set_reverb(self, value: float):
        with self._lock:
            self.reverb_mix = value
            for fx in self.board_on:
                if isinstance(fx, Reverb):
                    fx.wet_level = value
                    fx.dry_level = max(0.0, 1.0 - value)
                    return
            self._build_boards()

    def set_gain(self, value: float):
        with self._lock:
            self.gain_db = value
            for fx in self.board_on:
                if isinstance(fx, Gain):
                    fx.gain_db = value
                    return
            self._build_boards()

    def set_semitones(self, value: float):
        with self._lock:
            self.semitones = value
            if _PYWORLD_AVAILABLE and self._session_cfg.enabled:
                self._session_cfg.pitch_shift = value

            # se WORLD è spento, aggiorna il PitchShift legacy
            else:
                self._build_boards()

    def set_enabled(self, value: bool):
        with self._lock:
            self.enabled = value

    def toggle(self) -> bool:
        with self._lock:
            self.enabled = not self.enabled
            return self.enabled

    def current_board(self):
        with self._lock:
            return self.board_on if self.enabled else self.board_off

    def session_cfg(self) -> VoicePrivacyConfig:
        with self._lock:
            return self._session_cfg

    def process_world(self, audio: np.ndarray, sr: int) -> np.ndarray:
        """
        Applica lo stadio WORLD se disponibile e l'alterazione e' attiva.
        In caso di qualsiasi eccezione imprevista, restituisce l'audio
        originale e stampa un avviso, senza mai rompere lo stream.
        """
        with self._lock:
            if not self.enabled:
                return audio
            if not (_PYWORLD_AVAILABLE and self._session_cfg.enabled):
                return audio
            cfg = self._session_cfg
        # Esecuzione fuori dal lock per non bloccare altri thread
        try:
            result = world_process(audio, sr, cfg)
        except Exception as exc:
            print(f"[WORLD] errore nel blocco audio, bypass: {exc}", file=sys.stderr)
            return audio
        # Guardia finale: assicura dtype e lunghezza corretti
        result = result.astype(np.float32)
        if len(result) != len(audio):
            # Non dovrebbe mai accadere dopo i fix, ma meglio avere un
            # fallback che rompere il buffer di sounddevice
            result = np.resize(result, len(audio))
        return result


# =============================================================================
# ENUMERAZIONE DISPOSITIVI (tramite sounddevice/PortAudio)
# =============================================================================

def _device_rows():
    hostapis = sd.query_hostapis()
    rows = []
    for idx, dev in enumerate(sd.query_devices()):
        hostapi_name = ""
        try:
            hostapi_name = hostapis[dev["hostapi"]]["name"]
        except Exception:
            pass
        rows.append({
            "index": idx,
            "name": dev["name"],
            "in_ch": dev["max_input_channels"],
            "out_ch": dev["max_output_channels"],
            "hostapi": hostapi_name,
            "default_samplerate": dev.get("default_samplerate", 48000),
        })
    return rows


def list_devices():
    rows = _device_rows()
    print("\n--- Dispositivi di INPUT disponibili (in_ch > 0) ---")
    for r in rows:
        if r["in_ch"] > 0:
            print(f"  [{r['index']:>2}] {r['name']}  (in_ch={r['in_ch']}, hostapi={r['hostapi']})")
    print("\n--- Dispositivi di OUTPUT disponibili (out_ch > 0) ---")
    for r in rows:
        if r["out_ch"] > 0:
            print(f"  [{r['index']:>2}] {r['name']}  (out_ch={r['out_ch']}, hostapi={r['hostapi']})")
    print()


def diagnose_devices():
    rows = _device_rows()
    print("\n=== Diagnostica dispositivi audio (sounddevice/PortAudio) ===\n")
    header = f"{'idx':<4} {'in_ch':<6} {'out_ch':<7} {'hostapi':<20} nome"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(f"{r['index']:<4} {r['in_ch']:<6} {r['out_ch']:<7} {r['hostapi']:<20} {r['name']}")

    print(
        "\nSuggerimenti se un dispositivo (es. Jabra) non appare con in_ch > 0:\n"
        "  - Windows: Impostazioni > Sistema > Suono > Ingresso: verifica che\n"
        "    il microfono sia elencato, abilitato e non disattivato da\n"
        "    un'altra app che lo tiene in uso esclusivo.\n"
        "  - Ubuntu: esegui 'pactl list sources short' oppure apri 'pavucontrol'\n"
        "    (scheda Input Devices) per verificare che la sorgente mic esista.\n"
        "  - Se il dispositivo e' via Bluetooth, alcuni profili (es. A2DP) offrono\n"
        "    solo l'uscita audio in alta qualita' e NON il microfono: serve\n"
        "    passare al profilo 'Headset/HFP'.\n"
        "  - Se compare piu' volte con hostapi diverse (es. sia WASAPI che\n"
        "    MME), preferisci la riga con hostapi 'Windows WASAPI': di solito\n"
        "    e' la piu' affidabile per dispositivi USB/Bluetooth.\n"
    )


def find_device_index(name_or_index, kind: str):
    if name_or_index is None:
        return None
    try:
        return int(name_or_index)
    except (TypeError, ValueError):
        pass

    query = str(name_or_index).strip().lower()
    rows = _device_rows()
    channel_key = "in_ch" if kind == "input" else "out_ch"

    for r in rows:
        if r[channel_key] > 0 and r["name"].strip().lower() == query:
            return r["index"]
    for r in rows:
        if r[channel_key] > 0 and query in r["name"].strip().lower():
            return r["index"]

    raise ValueError(
        f"Nessun dispositivo di {kind} trovato per '{name_or_index}'. "
        f"Usa --list-devices o --diagnose per vedere i nomi/indici disponibili."
    )


# =============================================================================
# MOTORE AUDIO (sounddevice per l'I/O + WORLD + Pedalboard per gli effetti)
# =============================================================================
class AudioEngine:
    """
    Gestisce lo stream audio full-duplex con sounddevice.
    """

    def __init__(
        self,
        anonymizer: VoiceAnonymizer,
        input_device,
        output_device,
        samplerate: int = 48000,
        blocksize: int = 1024,
    ):
        self.anonymizer = anonymizer
        self.samplerate = samplerate
        
        # Nuove variabili per esporre il livello audio alla GUI
        self.current_in_level = 0.0
        self.current_out_level = 0.0

        in_info = sd.query_devices(input_device)
        out_info = sd.query_devices(output_device)
        self.in_channels = max(1, min(2, in_info["max_input_channels"]))
        self.out_channels = max(1, min(2, out_info["max_output_channels"]))

        self.stream = sd.Stream(
            device=(input_device, output_device),
            samplerate=samplerate,
            blocksize=blocksize,
            dtype="float32",
            channels=(self.in_channels, self.out_channels),
            callback=self._callback,
        )

    def _callback(self, indata, outdata, frames, time_info, status):
        if status:
            print(status, file=sys.stderr)

        try:
            # 1. Mixdown a mono
            mono = indata.mean(axis=1).astype(np.float32)
            
            # --- Calcolo RMS Input per la GUI ---
            self.current_in_level = float(np.sqrt(np.mean(mono**2)))

            # 2. Stadio WORLD
            mono = self.anonymizer.process_world(mono, self.samplerate)

            # 3. Stadio Pedalboard
            board = self.anonymizer.current_board()
            processed = board(mono.reshape(1, -1), self.samplerate, reset=False)
            processed = processed.reshape(-1)

            # Assicura lunghezza corretta (paranoia difensiva)
            if len(processed) < frames:
                processed = np.pad(processed, (0, frames - len(processed)))
            elif len(processed) > frames:
                processed = processed[:frames]
                
            # --- Calcolo RMS Output per la GUI ---
            self.current_out_level = float(np.sqrt(np.mean(processed**2)))

            # 4. Output su tutti i canali richiesti
            outdata[:] = np.tile(processed.reshape(-1, 1), (1, self.out_channels))

        except Exception as exc:
            outdata.fill(0)
            self.current_in_level = 0.0
            self.current_out_level = 0.0
            print(f"[AudioEngine] errore nel callback, silenzio: {exc}", file=sys.stderr)

    def start(self):
        self.stream.start()

    def stop(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
        finally:
            self.current_in_level = 0.0
            self.current_out_level = 0.0

# =============================================================================
# MODALITA' RIGA DI COMANDO
# =============================================================================

def console_control_loop(anonymizer: VoiceAnonymizer):
    print("\nComandi disponibili (digita e premi invio):")
    print("  t  -> attiva/disattiva l'alterazione vocale")
    print("  q  -> esci dal programma\n")
    while True:
        try:
            cmd = input().strip().lower()
        except EOFError:
            time.sleep(0.5)
            continue
        if cmd == "t":
            enabled = anonymizer.toggle()
            stato = "ATTIVA (voce alterata)" if enabled else "DISATTIVATA (voce originale)"
            print(f"[Alterazione vocale: {stato}]")
        elif cmd == "q":
            print("Uscita...")
            os._exit(0)


def run_cli(args):
    if not args.input or not args.output:
        print("Errore: specifica --input e --output.")
        print("Usa 'python voice_anonymizer.py --list-devices' per vedere i nomi/indici disponibili.")
        sys.exit(1)

    try:
        input_idx = find_device_index(args.input, "input")
        output_idx = find_device_index(args.output, "output")
    except ValueError as exc:
        print(f"Errore: {exc}")
        sys.exit(1)

    privacy_cfg = VoicePrivacyConfig(
        enabled=True,
        pitch_shift=args.semitones,
        formant_scale=args.formant_scale,
        randomize=args.randomize,
        pitch_variation=args.pitch_variation,
        formant_variation=args.formant_variation,
        spectral_tilt=args.spectral_tilt,
        time_scale=args.time_scale,
        noise_floor_dbfs=args.noise_floor if args.noise_floor != 0 else None,
    )

    anonymizer = VoiceAnonymizer(
        semitones=args.semitones,
        chorus_mix=args.chorus_mix,
        enabled=not args.start_disabled,
        privacy_cfg=privacy_cfg,
    )

    print(f"Input : [{input_idx}] {sd.query_devices(input_idx)['name']}")
    print(f"Output: [{output_idx}] {sd.query_devices(output_idx)['name']}")
    print(f"Chorus mix: {args.chorus_mix}")
    print(f"Stato iniziale: {'ATTIVA' if anonymizer.enabled else 'DISATTIVATA'}")

    engine = AudioEngine(
        anonymizer, input_idx, output_idx,
        samplerate=args.samplerate, blocksize=args.buffer_size,
    )
    engine.start()
    print("\nStream audio avviato. Ctrl+C per fermare.")

    control_thread = threading.Thread(
        target=console_control_loop, args=(anonymizer,), daemon=True
    )
    control_thread.start()

    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nInterrotto dall'utente.")
    finally:
        engine.stop()


# =============================================================================
# MODALITA' GRAFICA (GUI) CON TOGGLE
# =============================================================================

def run_gui(default_semitones: float = -4.0, default_chorus_mix: float = 0.2):
    import tkinter as tk
    from tkinter import ttk, messagebox

    class ToggleSwitch(tk.Canvas):
        """Piccolo interruttore on/off disegnato a mano (stile switch)."""

        WIDTH = 56
        HEIGHT = 28
        COLOR_ON = "#4CAF50"
        COLOR_OFF = "#B0B0B0"

        def __init__(self, parent, initial=True, command=None, **kwargs):
            bg = kwargs.pop("bg", None)
            if bg is None:
                try:
                    bg = ttk.Style().lookup("TFrame", "background") or "#F0F0F0"
                except tk.TclError:
                    bg = "#F0F0F0"
            super().__init__(
                parent, width=self.WIDTH, height=self.HEIGHT,
                highlightthickness=0, bg=bg, **kwargs
            )
            self.state = initial
            self.command = command
            self.bind("<Button-1>", self._on_click)
            self.configure(cursor="hand2")
            self._draw()

        def _round_rect(self, x1, y1, x2, y2, radius, **kw):
            points = [
                x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
                x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
                x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
            ]
            return self.create_polygon(points, smooth=True, **kw)

        def _draw(self):
            self.delete("all")
            radius = self.HEIGHT / 2
            color = self.COLOR_ON if self.state else self.COLOR_OFF
            self._round_rect(2, 2, self.WIDTH - 2, self.HEIGHT - 2, radius, fill=color, outline="")
            knob_d = self.HEIGHT - 6
            knob_x = (self.WIDTH - 3 - knob_d) if self.state else 3
            self.create_oval(knob_x, 3, knob_x + knob_d, 3 + knob_d, fill="white", outline="")

        def _on_click(self, _event):
            self.set_state(not self.state, fire_command=True)

        def set_state(self, value: bool, fire_command: bool = False):
            self.state = value
            self._draw()
            if fire_command and self.command:
                self.command(self.state)

    class App:
        def __init__(self, root):
            self.root = root
            root.title("Voice Anonymizer - Avanzato")
            root.resizable(False, False)

            saved_config = load_config()
            
            loaded_semitones = saved_config.get("semitones", default_semitones)
            loaded_formant = saved_config.get("formant_scale", 1.10)
            loaded_chorus = saved_config.get("chorus_mix", 0.03)
            loaded_reverb = saved_config.get("reverb_mix", 0.15)
            loaded_gain = saved_config.get("gain_db", 0.0)
            loaded_world = saved_config.get("world_enabled", True)
            loaded_hpf = saved_config.get("hpf_cutoff", 150)
            loaded_lpf = saved_config.get("lpf_cutoff", 6000)
            loaded_comp_t = saved_config.get("comp_threshold", -22.0)
            loaded_comp_r = saved_config.get("comp_ratio", 3.0)

            privacy_cfg = VoicePrivacyConfig(
                enabled=loaded_world,
                pitch_shift=loaded_semitones,
                formant_scale=loaded_formant,
                randomize=True,
            )
            self.anonymizer = VoiceAnonymizer(
                semitones=loaded_semitones,
                chorus_mix=loaded_chorus,
                reverb_mix=loaded_reverb,
                gain_db=loaded_gain,
                world_enabled=loaded_world,
                hpf_cutoff=loaded_hpf,
                lpf_cutoff=loaded_lpf,
                comp_threshold=loaded_comp_t,
                comp_ratio=loaded_comp_r,
                enabled=True,
                privacy_cfg=privacy_cfg,
            )
            self.engine = None

            self.device_rows = _device_rows()
            self.input_options = [
                f"[{r['index']}] {r['name']}  ({r['hostapi']})"
                for r in self.device_rows if r["in_ch"] > 0
            ]
            self.output_options = [
                f"[{r['index']}] {r['name']}  ({r['hostapi']})"
                for r in self.device_rows if r["out_ch"] > 0
            ]

            pad = {"padx": 6, "pady": 3}
            frame = ttk.Frame(root, padding=12)
            frame.grid()

            # Dispositivi Input / Output
            ttk.Label(frame, text="Microfono (input):").grid(row=0, column=0, sticky="w", **pad)
            default_in = saved_config.get("input_device", self.input_options[0] if self.input_options else "")
            self.input_var = tk.StringVar(value=default_in)
            self.input_combo = ttk.Combobox(frame, textvariable=self.input_var, values=self.input_options, width=38, state="readonly")
            self.input_combo.grid(row=0, column=1, columnspan=2, **pad)

            ttk.Label(frame, text="Dispositivo virtuale (output):").grid(row=1, column=0, sticky="w", **pad)
            default_out = saved_config.get("output_device", self.output_options[0] if self.output_options else "")
            self.output_var = tk.StringVar(value=default_out)
            self.output_combo = ttk.Combobox(frame, textvariable=self.output_var, values=self.output_options, width=38, state="readonly")
            self.output_combo.grid(row=1, column=1, columnspan=2, **pad)

            # Pulsanti Auto-Rileva e Salva
            btn_frame = ttk.Frame(frame)
            btn_frame.grid(row=0, column=3, rowspan=2, padx=8)
            ttk.Button(btn_frame, text="Auto Rileva", command=self._auto_detect).pack(fill="x", pady=2)
            ttk.Button(btn_frame, text="Salva Impostazioni", command=self._save_settings).pack(fill="x", pady=2)

            row_idx = 2

            # --- Checkbox per abilitare/disabilitare WORLD ---
            self.world_var = tk.BooleanVar(value=loaded_world)
            self.world_check = ttk.Checkbutton(frame, text="Abilita Vocoder WORLD (Robotico/Avanzato)", variable=self.world_var, command=self._on_world_toggle)
            self.world_check.grid(row=row_idx, column=0, columnspan=3, sticky="w", **pad)
            row_idx += 1

            # Slider Pitch Shift
            ttk.Label(frame, text="Pitch shift (semitoni):").grid(row=row_idx, column=0, sticky="w", **pad)
            self.semitones_var = tk.DoubleVar(value=loaded_semitones)
            self.semitones_scale = ttk.Scale(frame, from_=-12, to=12, variable=self.semitones_var, command=self._on_semitones_change, length=180)
            self.semitones_scale.grid(row=row_idx, column=1, sticky="we", **pad)
            self.semitones_label = ttk.Label(frame, text=f"{loaded_semitones:.1f}", width=5)
            self.semitones_label.grid(row=row_idx, column=2, sticky="w", **pad)
            row_idx += 1

            # Slider Formant Scale
            if _PYWORLD_AVAILABLE:
                ttk.Label(frame, text="Formant scale:").grid(row=row_idx, column=0, sticky="w", **pad)
                self.formant_var = tk.DoubleVar(value=loaded_formant)
                self.formant_scale_slider = ttk.Scale(frame, from_=0.80, to=1.30, variable=self.formant_var, command=self._on_formant_change, length=180)
                self.formant_scale_slider.grid(row=row_idx, column=1, sticky="we", **pad)
                self.formant_label = ttk.Label(frame, text=f"{loaded_formant:.2f}", width=5)
                self.formant_label.grid(row=row_idx, column=2, sticky="w", **pad)
                row_idx += 1

            # Slider Chorus
            ttk.Label(frame, text="Chorus Mix:").grid(row=row_idx, column=0, sticky="w", **pad)
            self.chorus_var = tk.DoubleVar(value=loaded_chorus)
            self.chorus_scale = ttk.Scale(frame, from_=0.0, to=0.3, variable=self.chorus_var, command=self._on_chorus_change, length=180)
            self.chorus_scale.grid(row=row_idx, column=1, sticky="we", **pad)
            self.chorus_label = ttk.Label(frame, text=f"{loaded_chorus:.2f}", width=5)
            self.chorus_label.grid(row=row_idx, column=2, sticky="w", **pad)
            row_idx += 1

            # Slider Riverbero
            ttk.Label(frame, text="Reverb (Stanza):").grid(row=row_idx, column=0, sticky="w", **pad)
            self.reverb_var = tk.DoubleVar(value=loaded_reverb)
            self.reverb_scale = ttk.Scale(frame, from_=0.0, to=0.5, variable=self.reverb_var, command=self._on_reverb_change, length=180)
            self.reverb_scale.grid(row=row_idx, column=1, sticky="we", **pad)
            self.reverb_label = ttk.Label(frame, text=f"{loaded_reverb:.2f}", width=5)
            self.reverb_label.grid(row=row_idx, column=2, sticky="w", **pad)
            row_idx += 1

            # --- NUOVO: Filtri e Compressore ---
            ttk.Label(frame, text="Filtro Taglio Bassi (HPF Hz):").grid(row=row_idx, column=0, sticky="w", **pad)
            self.hpf_var = tk.IntVar(value=loaded_hpf)
            self.hpf_scale = ttk.Scale(frame, from_=0, to=300, variable=self.hpf_var, command=self._on_dsp_change, length=180)
            self.hpf_scale.grid(row=row_idx, column=1, sticky="we", **pad)
            self.hpf_label = ttk.Label(frame, text=f"{loaded_hpf}", width=5)
            self.hpf_label.grid(row=row_idx, column=2, sticky="w", **pad)
            row_idx += 1

            ttk.Label(frame, text="Filtro Taglio Alti (LPF Hz):").grid(row=row_idx, column=0, sticky="w", **pad)
            self.lpf_var = tk.IntVar(value=loaded_lpf)
            self.lpf_scale = ttk.Scale(frame, from_=2000, to=10000, variable=self.lpf_var, command=self._on_dsp_change, length=180)
            self.lpf_scale.grid(row=row_idx, column=1, sticky="we", **pad)
            self.lpf_label = ttk.Label(frame, text=f"{loaded_lpf}", width=5)
            self.lpf_label.grid(row=row_idx, column=2, sticky="w", **pad)
            row_idx += 1

            ttk.Label(frame, text="Comp. Soglia (dB):").grid(row=row_idx, column=0, sticky="w", **pad)
            self.comp_t_var = tk.DoubleVar(value=loaded_comp_t)
            self.comp_t_scale = ttk.Scale(frame, from_=-40, to=0, variable=self.comp_t_var, command=self._on_dsp_change, length=180)
            self.comp_t_scale.grid(row=row_idx, column=1, sticky="we", **pad)
            self.comp_t_label = ttk.Label(frame, text=f"{loaded_comp_t:.1f}", width=5)
            self.comp_t_label.grid(row=row_idx, column=2, sticky="w", **pad)
            row_idx += 1

            # Slider Volume (Gain)
            ttk.Label(frame, text="Volume Output (dB):").grid(row=row_idx, column=0, sticky="w", **pad)
            self.gain_var = tk.DoubleVar(value=loaded_gain)
            self.gain_scale = ttk.Scale(frame, from_=-12.0, to=12.0, variable=self.gain_var, command=self._on_gain_change, length=180)
            self.gain_scale.grid(row=row_idx, column=1, sticky="we", **pad)
            self.gain_label = ttk.Label(frame, text=f"{loaded_gain:.1f}", width=5)
            self.gain_label.grid(row=row_idx, column=2, sticky="w", **pad)
            row_idx += 1

            # Bottoni Avvia e Toggle Switch
            btn_row = row_idx
            self.start_button = ttk.Button(frame, text="Avvia", command=self._toggle_stream)
            self.start_button.grid(row=btn_row, column=0, **pad)

            toggle_frame = ttk.Frame(frame)
            toggle_frame.grid(row=btn_row, column=1, columnspan=2, sticky="w", **pad)
            ttk.Label(toggle_frame, text="Alterazione vocale:").pack(side="left", padx=(0, 6))
            self.toggle = ToggleSwitch(toggle_frame, initial=True, command=self._on_toggle)
            self.toggle.pack(side="left")

            # Status e Meter
            ttk.Label(frame, text="Pipeline: Gestione Avanzata DSP", foreground="#1565C0").grid(row=btn_row + 1, column=0, columnspan=3, sticky="w", **pad)
            self.status_label = ttk.Label(frame, text="Stream non avviato", foreground="gray")
            self.status_label.grid(row=btn_row + 2, column=0, columnspan=3, sticky="w", **pad)

            meter_frame = ttk.LabelFrame(frame, text="Livelli Segnale", padding=6)
            meter_frame.grid(row=btn_row + 3, column=0, columnspan=3, sticky="we", **pad)
            
            ttk.Label(meter_frame, text="Mic (In):").grid(row=0, column=0, sticky="w")
            self.in_meter = ttk.Progressbar(meter_frame, orient="horizontal", length=220, mode="determinate")
            self.in_meter.grid(row=0, column=1, padx=6, pady=2, sticky="we")

            ttk.Label(meter_frame, text="Virtual (Out):").grid(row=1, column=0, sticky="w")
            self.out_meter = ttk.Progressbar(meter_frame, orient="horizontal", length=220, mode="determinate")
            self.out_meter.grid(row=1, column=1, padx=6, pady=2, sticky="we")

            root.protocol("WM_DELETE_WINDOW", self._on_close)
            self._update_meters()

        def _rms_to_percent(self, rms: float) -> float:
            """Mappa un valore RMS lineare su una scala visiva (pseudo-logaritmica)."""
            if rms < 1e-5:
                return 0.0
            db = 20 * np.log10(rms)
            # Mappiamo arbitrariamente: -50 dB = 0%, 0 dB = 100%
            percent = (db + 50) * 2
            return max(0.0, min(100.0, percent))

        def _update_meters(self):
            """Aggiorna le barre della GUI interrogando l'AudioEngine in modo sicuro."""
            if self.engine is not None:
                in_val = self._rms_to_percent(self.engine.current_in_level)
                out_val = self._rms_to_percent(self.engine.current_out_level)
                self.in_meter["value"] = in_val
                self.out_meter["value"] = out_val
            else:
                self.in_meter["value"] = 0
                self.out_meter["value"] = 0
                
            # Ripianifica l'aggiornamento a ~20 FPS
            self.root.after(50, self._update_meters)

        def _extract_index(self, combo_value: str):
            try:
                return int(combo_value.split("]")[0].lstrip("["))
            except (ValueError, IndexError):
                return None

        def _on_semitones_change(self, value_str):
            value = float(value_str)
            self.semitones_label.config(text=f"{value:.1f}")
            self.anonymizer.set_semitones(value)

        def _on_formant_change(self, value_str):
            value = float(value_str)
            if hasattr(self, "formant_label"):
                self.formant_label.config(text=f"{value:.2f}")
            with self.anonymizer._lock:
                self.anonymizer._session_cfg.formant_scale = value

        def _on_toggle(self, enabled: bool):
            self.anonymizer.set_enabled(enabled)
            self._update_status()

        def _toggle_stream(self):
            if self.engine is None:
                input_idx = self._extract_index(self.input_var.get())
                output_idx = self._extract_index(self.output_var.get())
                if input_idx is None or output_idx is None:
                    messagebox.showerror(
                        "Errore",
                        "Seleziona sia il microfono di input sia il dispositivo di output.",
                    )
                    return
                try:
                    self.engine = AudioEngine(self.anonymizer, input_idx, output_idx)
                    self.engine.start()
                except Exception as exc:
                    messagebox.showerror("Errore", f"Impossibile avviare lo stream audio:\n{exc}")
                    self.engine = None
                    return
                self.start_button.config(text="Ferma")
                self.input_combo.config(state="disabled")
                self.output_combo.config(state="disabled")
            else:
                self.engine.stop()
                self.engine = None
                self.start_button.config(text="Avvia")
                self.input_combo.config(state="readonly")
                self.output_combo.config(state="readonly")
            self._update_status()

        def _update_status(self):
            if self.engine is None:
                self.status_label.config(text="Stream non avviato", foreground="gray")
            else:
                stato = "ATTIVA (voce alterata)" if self.anonymizer.enabled else "DISATTIVATA (voce originale)"
                colore = "#2E7D32" if self.anonymizer.enabled else "#757575"
                self.status_label.config(
                    text=f"Stream attivo — Alterazione: {stato}", foreground=colore
                )

        def _on_close(self):
            if self.engine is not None:
                self.engine.stop()
            self.root.destroy()

        def _auto_detect(self):
            """Interroga il sistema operativo per i dispositivi predefiniti attivi"""
            in_idx, out_idx = sd.default.device
            if in_idx is not None:
                for opt in self.input_options:
                    if opt.startswith(f"[{in_idx}]"):
                        self.input_var.set(opt)
                        break
            if out_idx is not None:
                for opt in self.output_options:
                    if opt.startswith(f"[{out_idx}]"):
                        self.output_var.set(opt)
                        break

        def _on_chorus_change(self, value_str):
            value = float(value_str)
            self.chorus_label.config(text=f"{value:.2f}")
            self.anonymizer.set_chorus(value)

        def _on_reverb_change(self, value_str):
            value = float(value_str)
            self.reverb_label.config(text=f"{value:.2f}")
            self.anonymizer.set_reverb(value)

        def _on_gain_change(self, value_str):
            value = float(value_str)
            self.gain_label.config(text=f"{value:.1f}")
            self.anonymizer.set_gain(value)

        def _on_world_toggle(self):
            val = self.world_var.get()
            self.anonymizer.set_world_enabled(val)

        def _on_dsp_change(self, _event=None):
            hpf_val = int(self.hpf_var.get())
            lpf_val = int(self.lpf_var.get())
            comp_t_val = float(self.comp_t_var.get())
            
            self.hpf_label.config(text=f"{hpf_val}")
            self.lpf_label.config(text=f"{lpf_val}")
            self.comp_t_label.config(text=f"{comp_t_val:.1f}")
            
            # Usiamo un rapporto di compressione fisso a 3.0 o lo lasciamo invariato
            self.anonymizer.update_dsp_settings(hpf_val, lpf_val, comp_t_val, 3.0)

        def _save_settings(self):
            formant_val = self.formant_var.get() if hasattr(self, "formant_var") else 1.10
            save_config(
                in_dev=self.input_var.get(),
                out_dev=self.output_var.get(),
                semitones=self.semitones_var.get(),
                formant=formant_val,
                chorus=self.chorus_var.get(),
                reverb=self.reverb_var.get(),
                gain=self.gain_var.get(),
                world_enabled=self.world_var.get(),
                hpf=int(self.hpf_var.get()),
                lpf=int(self.lpf_var.get()),
                comp_thresh=float(self.comp_t_var.get()),
                comp_ratio=3.0
            )
            messagebox.showinfo("Configurazione", "Tutte le impostazioni avanzate e i filtri sono stati salvati!")

    root = tk.Tk()
    App(root)
    root.mainloop()


# =============================================================================
# ENTRY POINT
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Alteratore vocale in tempo reale per anonimizzare l'impronta vocale nelle riunioni."
    )
    parser.add_argument("--gui", action="store_true",
                        help="Avvia l'interfaccia grafica (default se non specifichi --input/--output)")
    parser.add_argument("--list-devices", action="store_true",
                        help="Elenca i dispositivi audio disponibili ed esce")
    parser.add_argument("--diagnose", action="store_true",
                        help="Diagnostica dettagliata dei dispositivi audio ed esce")
    parser.add_argument("--input", type=str,
                        help="Nome (anche parziale) o indice del dispositivo di INPUT")
    parser.add_argument("--output", type=str,
                        help="Nome (anche parziale) o indice del dispositivo di OUTPUT")

    parser.add_argument("--chorus-mix", type=float, default=0.15,
                        help="Intensita' del chorus (0.0-1.0) (default: 0.15)")
    parser.add_argument("--samplerate", type=int, default=48000,
                        help="Frequenza di campionamento (default: 48000)")
    parser.add_argument("--buffer-size", type=int, default=1024,
                        help="Dimensione buffer audio (default: 1024)")
    parser.add_argument("--start-disabled", action="store_true",
                        help="Avvia con l'alterazione disattivata (bypass)")

    parser.add_argument("--semitones", type=float, default=-2.5,
                        help="Pitch shift in semitoni (default: -2.5)")
    parser.add_argument("--formant-scale", type=float, default=1.10,
                        help="Scala dei formanti (default: 1.10)")
    parser.add_argument("--no-randomize", action="store_true",
                        help="Disabilita la randomizzazione per-sessione")
    parser.add_argument("--pitch-variation", type=float, default=0.3,
                        help="Variazione casuale pitch in semitoni (default: 0.3)")
    parser.add_argument("--formant-variation", type=float, default=0.03,
                        help="Variazione casuale formant scale (default: 0.03)")
    parser.add_argument("--spectral-tilt", type=float, default=0.0,
                        help="Tilt spettrale in dB/ottava (default: 0.0)")
    parser.add_argument("--time-scale", type=float, default=1.0,
                        help="Micro time-stretch (default: 1.0 = nessuno)")
    parser.add_argument("--noise-floor", type=float, default=-55.0,
                        help="Noise floor in dBFS (default: -55). 0 = disabilitato.")
    parser.add_argument("--check-defaults", action="store_true",
                        help="Mostra i dispositivi predefiniti di sistema in uso ed esce")
    args = parser.parse_args()
    args.randomize = not args.no_randomize

    if args.diagnose:
        diagnose_devices()
        sys.exit(0)

    if args.list_devices:
        list_devices()
        sys.exit(0)

    if args.check_defaults:
        in_idx, out_idx = sd.default.device
        print("\n--- Dispositivi Predefiniti di Sistema (Attualmente in uso) ---")
        if in_idx is not None:
            print(f"  INPUT : [{in_idx}] {sd.query_devices(in_idx)['name']}")
        else:
            print("  INPUT : Nessun dispositivo predefinito trovato.")
            
        if out_idx is not None:
            print(f"  OUTPUT: [{out_idx}] {sd.query_devices(out_idx)['name']}")
        else:
            print("  OUTPUT: Nessun dispositivo predefinito trovato.")
        print()
        sys.exit(0)

    if args.gui or not (args.input and args.output):
        run_gui(default_semitones=args.semitones, default_chorus_mix=args.chorus_mix)
    else:
        run_cli(args)


if __name__ == "__main__":
    main()