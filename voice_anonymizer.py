"""
====================

Alteratore vocale in tempo reale, pensato per anonimizzare l'impronta
vocale durante riunioni Microsoft Teams (o qualsiasi altra app) che
vengono registrate/trascritte.

Funziona su Windows e Ubuntu/Linux. Non si collega direttamente a
Teams: legge l'audio dal tuo microfono reale, lo altera e lo scrive
su un MICROFONO VIRTUALE, che poi selezioni come input in Teams.
Vedi le istruzioni di setup in fondo al file.

Dipendenze Python:
    pip install pedalboard

(pedalboard, sviluppato da Spotify, gestisce l'I/O audio in tempo
reale con PortAudio ed effetti DSP di qualità professionale, quindi
non servono altre librerie audio.)

Uso tipico:
    # 1. Elenca i dispositivi audio disponibili
    python voice_anonymizer.py --list-devices

    # 2. Avvia lo stream (esempio Windows con VB-CABLE)
    python voice_anonymizer.py --input "Microfono (Realtek Audio)" --output "CABLE Input (VB-Audio Virtual Cable)"

    # 2bis. Esempio Ubuntu/Linux con sink virtuale PulseAudio
    python voice_anonymizer.py --input "default" --output "VoiceAnonymizer_Sink"

Durante l'esecuzione, nel terminale puoi digitare:
    t  + invio   -> attiva/disattiva l'alterazione (bypass <-> alterata)
    q  + invio   -> esce dal programma
"""

import argparse
import os
import sys
import threading
import time

from pedalboard import (
    Pedalboard,
    PitchShift,
    HighpassFilter,
    LowpassFilter,
    Chorus,
    Gain,
)
from pedalboard.io import AudioStream


class VoiceAnonymizer:
    """Incapsula la catena di effetti e lo stato attivo/disattivo."""

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


def list_devices():
    print("\n--- Dispositivi di INPUT disponibili ---")
    for name in AudioStream.input_device_names:
        print(f"  {name}")
    print("\n--- Dispositivi di OUTPUT disponibili ---")
    for name in AudioStream.output_device_names:
        print(f"  {name}")
    print()


def console_control_loop(anonymizer: VoiceAnonymizer, stream: AudioStream):
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
            stream.plugins = anonymizer.current_board()
            stato = "ATTIVA (voce alterata)" if enabled else "DISATTIVATA (voce originale)"
            print(f"[Alterazione vocale: {stato}]")
        elif cmd == "q":
            print("Uscita...")
            os._exit(0)


def main():
    parser = argparse.ArgumentParser(
        description="Alteratore vocale in tempo reale per anonimizzare l'impronta vocale nelle riunioni."
    )
    parser.add_argument("--list-devices", action="store_true",
                         help="Elenca i dispositivi audio disponibili ed esce")
    parser.add_argument("--input", type=str,
                         help="Nome del dispositivo di INPUT (il tuo microfono reale)")
    parser.add_argument("--output", type=str,
                         help="Nome del dispositivo di OUTPUT (il cavo/microfono virtuale usato da Teams)")
    parser.add_argument("--semitones", type=float, default=-4.0,
                         help="Pitch-shift in semitoni: negativo = voce più grave, positivo = più acuta (default: -4.0)")
    parser.add_argument("--chorus-mix", type=float, default=0.2,
                         help="Intensità del chorus (0.0-1.0), aggiunge variazione timbrica (default: 0.2)")
    parser.add_argument("--samplerate", type=int, default=48000,
                         help="Frequenza di campionamento (default: 48000)")
    parser.add_argument("--buffer-size", type=int, default=512,
                         help="Dimensione del buffer audio; valori più bassi = meno latenza ma più rischio di glitch (default: 512)")
    parser.add_argument("--start-disabled", action="store_true",
                         help="Avvia con l'alterazione disattivata (bypass)")
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        sys.exit(0)

    if not args.input or not args.output:
        print("Errore: specifica --input e --output.")
        print("Usa 'python voice_anonymizer.py --list-devices' per vedere i nomi esatti dei dispositivi.")
        sys.exit(1)

    anonymizer = VoiceAnonymizer(
        semitones=args.semitones,
        chorus_mix=args.chorus_mix,
        enabled=not args.start_disabled,
    )

    print(f"Input : {args.input}")
    print(f"Output: {args.output}")
    print(f"Pitch shift: {args.semitones} semitoni | Chorus mix: {args.chorus_mix}")
    print(f"Stato iniziale: {'ATTIVA' if anonymizer.enabled else 'DISATTIVATA'}")

    with AudioStream(
        input_device_name=args.input,
        output_device_name=args.output,
        sample_rate=args.samplerate,
        buffer_size=args.buffer_size,
    ) as stream:
        stream.plugins = anonymizer.current_board()
        print("\nStream audio avviato. Ctrl+C per fermare.")

        control_thread = threading.Thread(
            target=console_control_loop, args=(anonymizer, stream), daemon=True
        )
        control_thread.start()

        try:
            while True:
                time.sleep(0.2)
        except KeyboardInterrupt:
            print("\nInterrotto dall'utente.")


if __name__ == "__main__":
    main()


# =============================================================================
# SETUP DEL MICROFONO VIRTUALE (necessario prima di usare lo script con Teams)
# =============================================================================
#
# Lo script legge dal tuo microfono reale e scrive su un dispositivo audio
# VIRTUALE. Teams poi userà quel dispositivo virtuale come microfono.
#
# --- WINDOWS ---
# 1. Installa VB-CABLE (gratuito): https://vb-audio.com/Cable/
#    Crea due dispositivi: "CABLE Input" (output) e "CABLE Output" (input).
# 2. Avvia lo script con:
#      --input "Nome del tuo microfono fisico"
#      --output "CABLE Input (VB-Audio Virtual Cable)"
# 3. In Teams: Impostazioni > Dispositivi > Microfono = "CABLE Output (VB-Audio Virtual Cable)"
#
# --- UBUNTU / LINUX (PulseAudio o PipeWire-Pulse) ---
# 1. Crea un sink virtuale e una sorgente di monitor associata:
#      pactl load-module module-null-sink sink_name=VoiceAnonymizer_Sink sink_properties=device.description=VoiceAnonymizer_Sink
# 2. Lo script scriverà su "VoiceAnonymizer_Sink" (--output), che genera
#    automaticamente una sorgente monitor "VoiceAnonymizer_Sink.monitor".
# 3. In Teams: Impostazioni > Dispositivi > Microfono = "Monitor of VoiceAnonymizer_Sink"
#    (puoi rinominarla/selezionarla anche da pavucontrol, scheda "Recording"/"Input Devices").
# 4. Avvia lo script con:
#      --input "nome del tuo microfono reale (es. default o alsa_input...)"
#      --output "VoiceAnonymizer_Sink"
#
# NOTA IMPORTANTE:
# In molte giurisdizioni informare i partecipanti che si sta alterando/
# anonimizzando la propria voce durante una registrazione è buona prassi
# di trasparenza, anche se lo scopo è proteggere i propri dati biometrici.
# =============================================================================