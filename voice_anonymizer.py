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

Motore audio: 'sounddevice' (basato su PortAudio) per l'I/O audio, e
'pedalboard' solo per la catena di effetti DSP (pitch-shift, filtri,
chorus). NOTA: le versioni precedenti di questo script usavano
pedalboard.io.AudioStream anche per l'I/O, ma quella API ha un bug
noto su Windows che a volte confonde i driver WASAPI/DirectSound e fa
sparire o duplicare dispositivi (vedi github.com/spotify/pedalboard,
issue #274). sounddevice si e' dimostrato piu' affidabile per
l'enumerazione dei dispositivi.

Dipendenze Python:
    pip install pedalboard sounddevice numpy
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

Diagnostica dispositivi (utile se un dispositivo non viene riconosciuto
correttamente come input o output):
    python voice_anonymizer.py --diagnose
"""

import argparse
import os
import sys
import threading
import time

import numpy as np
import sounddevice as sd

from pedalboard import (
    Pedalboard,
    PitchShift,
    HighpassFilter,
    LowpassFilter,
    Chorus,
    Gain,
)


# =============================================================================
# LOGICA DI ALTERAZIONE VOCALE (condivisa tra CLI e GUI)
# =============================================================================

class VoiceAnonymizer:
    """Incapsula la catena di effetti pedalboard e lo stato attivo/disattivo."""

    def __init__(self, semitones: float = -4.0, chorus_mix: float = 0.2, enabled: bool = True):
        self.semitones = semitones
        self.chorus_mix = chorus_mix
        self.enabled = enabled
        self._lock = threading.Lock()
        self._build_boards()

    def _build_boards(self):
        # Catena "alterata": pitch-shift per cambiare l'intonazione di base
        # (maschera il fondamentale della voce) + filtri passa-alto/basso
        # per spostare leggermente il timbro/formanti + un chorus leggero
        # per aggiungere micro-variazioni che confondono il riconoscimento
        # del parlante, mantenendo comunque il parlato intelligibile per
        # la trascrizione automatica.
        self.board_on = Pedalboard([
            PitchShift(semitones=self.semitones),
            HighpassFilter(cutoff_frequency_hz=140),
            LowpassFilter(cutoff_frequency_hz=7000),
            Chorus(rate_hz=0.7, depth=0.15, mix=self.chorus_mix),
            Gain(gain_db=0.0),
        ])
        # Catena "bypass": nessun effetto, audio originale invariato
        self.board_off = Pedalboard([])

    def set_semitones(self, value: float):
        with self._lock:
            self.semitones = value
            self._build_boards()

    def set_enabled(self, value: bool):
        with self._lock:
            self.enabled = value

    def toggle(self):
        with self._lock:
            self.enabled = not self.enabled
            return self.enabled

    def current_board(self):
        with self._lock:
            return self.board_on if self.enabled else self.board_off


# =============================================================================
# ENUMERAZIONE DISPOSITIVI (tramite sounddevice/PortAudio)
# =============================================================================

def _device_rows():
    """Ritorna una lista di dict con le info di ogni dispositivo audio."""
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
    """Diagnostica completa: mostra tutti i dispositivi con tutti i dettagli."""
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
    """
    Risolve un nome (anche parziale, case-insensitive) o un indice numerico
    in un indice di dispositivo valido per 'kind' ('input' oppure 'output').
    """
    if name_or_index is None:
        return None
    # Indice numerico esplicito
    try:
        return int(name_or_index)
    except (TypeError, ValueError):
        pass

    query = str(name_or_index).strip().lower()
    rows = _device_rows()
    channel_key = "in_ch" if kind == "input" else "out_ch"

    # Prima cerca una corrispondenza esatta, poi una parziale
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
# MOTORE AUDIO (sounddevice per l'I/O + pedalboard per gli effetti)
# =============================================================================

class AudioEngine:
    """
    Gestisce lo stream audio full-duplex con sounddevice, applicando ad
    ogni blocco la catena di effetti fornita da VoiceAnonymizer.
    """

    def __init__(self, anonymizer: VoiceAnonymizer, input_device, output_device,
                 samplerate: int = 48000, blocksize: int = 1024):
        self.anonymizer = anonymizer
        self.samplerate = samplerate

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

        # Mixdown a mono per l'elaborazione (la voce e' comunque mono)
        mono = indata.mean(axis=1).astype(np.float32)

        board = self.anonymizer.current_board()
        # pedalboard si aspetta shape (canali, campioni); usiamo 1 canale
        processed = board(mono.reshape(1, -1), self.samplerate, reset=False)
        processed = processed.reshape(-1)

        # Duplica il mono elaborato su tutti i canali di output richiesti
        outdata[:] = np.tile(processed.reshape(-1, 1), (1, self.out_channels))

    def start(self):
        self.stream.start()

    def stop(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass


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

    anonymizer = VoiceAnonymizer(
        semitones=args.semitones,
        chorus_mix=args.chorus_mix,
        enabled=not args.start_disabled,
    )

    print(f"Input : [{input_idx}] {sd.query_devices(input_idx)['name']}")
    print(f"Output: [{output_idx}] {sd.query_devices(output_idx)['name']}")
    print(f"Pitch shift: {args.semitones} semitoni | Chorus mix: {args.chorus_mix}")
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
            root.title("Voice Anonymizer")
            root.resizable(False, False)

            self.anonymizer = VoiceAnonymizer(
                semitones=default_semitones,
                chorus_mix=default_chorus_mix,
                enabled=True,
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

            pad = {"padx": 8, "pady": 6}
            frame = ttk.Frame(root, padding=16)
            frame.grid()

            ttk.Label(frame, text="Microfono (input):").grid(row=0, column=0, sticky="w", **pad)
            self.input_var = tk.StringVar(value=self.input_options[0] if self.input_options else "")
            self.input_combo = ttk.Combobox(
                frame, textvariable=self.input_var, values=self.input_options,
                width=46, state="readonly",
            )
            self.input_combo.grid(row=0, column=1, columnspan=2, **pad)

            ttk.Label(frame, text="Dispositivo virtuale (output):").grid(row=1, column=0, sticky="w", **pad)
            self.output_var = tk.StringVar(value=self.output_options[0] if self.output_options else "")
            self.output_combo = ttk.Combobox(
                frame, textvariable=self.output_var, values=self.output_options,
                width=46, state="readonly",
            )
            self.output_combo.grid(row=1, column=1, columnspan=2, **pad)

            ttk.Label(frame, text="Pitch shift (semitoni):").grid(row=2, column=0, sticky="w", **pad)
            self.semitones_var = tk.DoubleVar(value=default_semitones)
            self.semitones_scale = ttk.Scale(
                frame, from_=-12, to=12, variable=self.semitones_var,
                command=self._on_semitones_change, length=220,
            )
            self.semitones_scale.grid(row=2, column=1, sticky="we", **pad)
            self.semitones_label = ttk.Label(frame, text=f"{default_semitones:.1f}", width=5)
            self.semitones_label.grid(row=2, column=2, sticky="w", **pad)

            self.start_button = ttk.Button(frame, text="Avvia", command=self._toggle_stream)
            self.start_button.grid(row=3, column=0, **pad)

            toggle_frame = ttk.Frame(frame)
            toggle_frame.grid(row=3, column=1, columnspan=2, sticky="w", **pad)
            ttk.Label(toggle_frame, text="Alterazione vocale:").pack(side="left", padx=(0, 8))
            self.toggle = ToggleSwitch(toggle_frame, initial=True, command=self._on_toggle)
            self.toggle.pack(side="left")

            self.status_label = ttk.Label(frame, text="Stream non avviato", foreground="gray")
            self.status_label.grid(row=4, column=0, columnspan=3, sticky="w", **pad)

            root.protocol("WM_DELETE_WINDOW", self._on_close)

        def _extract_index(self, combo_value: str):
            # Il valore combobox e' del tipo "[3] Nome dispositivo  (hostapi)"
            try:
                return int(combo_value.split("]")[0].lstrip("["))
            except (ValueError, IndexError):
                return None

        def _on_semitones_change(self, value_str):
            value = float(value_str)
            self.semitones_label.config(text=f"{value:.1f}")
            self.anonymizer.set_semitones(value)

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
                self.status_label.config(text=f"Stream attivo — Alterazione: {stato}", foreground=colore)

        def _on_close(self):
            if self.engine is not None:
                self.engine.stop()
            self.root.destroy()

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
                         help="Nome (anche parziale) o indice del dispositivo di INPUT — modalita' CLI")
    parser.add_argument("--output", type=str,
                         help="Nome (anche parziale) o indice del dispositivo di OUTPUT — modalita' CLI")
    parser.add_argument("--semitones", type=float, default=-4.0,
                         help="Pitch-shift in semitoni: negativo = voce piu' grave, positivo = piu' acuta (default: -4.0)")
    parser.add_argument("--chorus-mix", type=float, default=0.2,
                         help="Intensita' del chorus (0.0-1.0) (default: 0.2)")
    parser.add_argument("--samplerate", type=int, default=48000,
                         help="Frequenza di campionamento (default: 48000)")
    parser.add_argument("--buffer-size", type=int, default=1024,
                         help="Dimensione del buffer audio; valori piu' bassi = meno latenza ma piu' rischio di glitch (default: 1024)")
    parser.add_argument("--start-disabled", action="store_true",
                         help="Avvia con l'alterazione disattivata (bypass) — modalita' CLI")
    args = parser.parse_args()

    if args.diagnose:
        diagnose_devices()
        sys.exit(0)

    if args.list_devices:
        list_devices()
        sys.exit(0)

    if args.gui or not (args.input and args.output):
        run_gui(default_semitones=args.semitones, default_chorus_mix=args.chorus_mix)
    else:
        run_cli(args)


if __name__ == "__main__":
    main()