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

Dipendenze Python:
    pip install pedalboard
    (tkinter e' incluso nella maggior parte delle installazioni Python;
     su Ubuntu potrebbe servire: sudo apt install python3-tk)

Due modalita' d'uso:

1) INTERFACCIA GRAFICA (consigliata)
    python voice_anonymizer.py
    oppure
    python voice_anonymizer.py --gui

2) RIGA DI COMANDO
    python voice_anonymizer.py --list-devices
    python voice_anonymizer.py --input "Microfono (Realtek Audio)" --output "CABLE Input (VB-Audio Virtual Cable)"

    Durante l'esecuzione da CLI puoi digitare:
        t  + invio   -> attiva/disattiva l'alterazione
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


# =============================================================================
# LOGICA DI ALTERAZIONE VOCALE (condivisa tra CLI e GUI)
# =============================================================================

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


def list_devices():
    print("\n--- Dispositivi di INPUT disponibili (pedalboard) ---")
    for name in AudioStream.input_device_names:
        print(f"  {name}")
    print("\n--- Dispositivi di OUTPUT disponibili (pedalboard) ---")
    for name in AudioStream.output_device_names:
        print(f"  {name}")
    print()


def diagnose_devices():
    """
    Diagnostica dettagliata: pedalboard mostra un elenco 'semplificato'
    dei dispositivi audio (un nome per input, uno per output). Se un
    dispositivo (es. un headset USB/Bluetooth come il Jabra) non compare
    dove ti aspetti, questa funzione usa la libreria 'sounddevice' per
    interrogare PortAudio a basso livello e mostrare TUTTI i dispositivi
    con il numero di canali di input/output e l'host API associato.
    Questo aiuta a capire il nome esatto da usare e se il sistema
    operativo sta davvero esponendo il microfono del dispositivo.

    Richiede: pip install sounddevice
    """
    print("\n=== Elenco 'semplificato' secondo pedalboard ===")
    list_devices()

    try:
        import sounddevice as sd
    except ImportError:
        print(
            "Per la diagnostica completa installa 'sounddevice':\n"
            "    pip install sounddevice\n"
            "poi rilancia: python voice_anonymizer.py --diagnose\n"
        )
        return

    print("=== Elenco dettagliato secondo sounddevice/PortAudio ===")
    print("(cerca il tuo dispositivo Jabra e controlla la colonna 'in_ch':")
    print(" se e' 0, il sistema operativo non lo espone come microfono)\n")

    try:
        hostapis = sd.query_hostapis()
    except Exception as exc:
        print(f"Impossibile interrogare le host API: {exc}")
        hostapis = []

    devices = sd.query_devices()
    header = f"{'idx':<4} {'in_ch':<6} {'out_ch':<7} {'hostapi':<20} nome"
    print(header)
    print("-" * len(header))
    for idx, dev in enumerate(devices):
        hostapi_name = ""
        try:
            hostapi_name = hostapis[dev["hostapi"]]["name"]
        except Exception:
            pass
        print(
            f"{idx:<4} {dev['max_input_channels']:<6} "
            f"{dev['max_output_channels']:<7} {hostapi_name:<20} {dev['name']}"
        )

    print(
        "\nSuggerimenti se il Jabra non appare con in_ch > 0 in NESSUNA riga:\n"
        "  - Windows: Impostazioni > Sistema > Suono > Ingresso: verifica che\n"
        "    il microfono Jabra sia elencato, abilitato e non disattivato da\n"
        "    un'altra app che lo tiene in uso esclusivo.\n"
        "  - Ubuntu: esegui 'pactl list sources short' oppure apri 'pavucontrol'\n"
        "    (scheda Input Devices) per verificare che la sorgente mic esista.\n"
        "  - Se il Jabra e' via Bluetooth, alcuni profili (es. A2DP) offrono\n"
        "    solo l'uscita audio in alta qualita' e NON il microfono: serve\n"
        "    passare al profilo 'Headset/HFP' (spesso qualita' audio minore)\n"
        "    per avere sia input che output microfono disponibili.\n"
        "  - Se invece appare con un nome DIVERSO da quello che ti aspetti\n"
        "    (es. 'Microfono (Jabra Evolve 65)' invece di 'Jabra Evolve 65'),\n"
        "    usa esattamente quel nome nel campo --input o nel menu della GUI.\n"
    )


# =============================================================================
# MODALITA' RIGA DI COMANDO
# =============================================================================

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


def run_cli(args):
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
                # I widget ttk non espongono "background" via cget come i
                # widget tk classici: il colore va letto dallo stile ttk,
                # altrimenti si usa un grigio chiaro di fallback.
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
            self.stream = None

            pad = {"padx": 8, "pady": 6}
            frame = ttk.Frame(root, padding=16)
            frame.grid()

            input_names = list(AudioStream.input_device_names)
            output_names = list(AudioStream.output_device_names)

            ttk.Label(frame, text="Microfono (input):").grid(row=0, column=0, sticky="w", **pad)
            self.input_var = tk.StringVar(value=input_names[0] if input_names else "")
            self.input_combo = ttk.Combobox(
                frame, textvariable=self.input_var, values=input_names,
                width=42, state="readonly",
            )
            self.input_combo.grid(row=0, column=1, columnspan=2, **pad)

            ttk.Label(frame, text="Dispositivo virtuale (output):").grid(row=1, column=0, sticky="w", **pad)
            self.output_var = tk.StringVar(value=output_names[0] if output_names else "")
            self.output_combo = ttk.Combobox(
                frame, textvariable=self.output_var, values=output_names,
                width=42, state="readonly",
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

        def _on_semitones_change(self, value_str):
            value = float(value_str)
            self.semitones_label.config(text=f"{value:.1f}")
            self.anonymizer.set_semitones(value)
            if self.stream is not None:
                self.stream.plugins = self.anonymizer.current_board()

        def _on_toggle(self, enabled: bool):
            self.anonymizer.set_enabled(enabled)
            if self.stream is not None:
                self.stream.plugins = self.anonymizer.current_board()
            self._update_status()

        def _toggle_stream(self):
            if self.stream is None:
                input_name = self.input_var.get()
                output_name = self.output_var.get()
                if not input_name or not output_name:
                    messagebox.showerror(
                        "Errore",
                        "Seleziona sia il microfono di input sia il dispositivo di output.",
                    )
                    return
                try:
                    self.stream = AudioStream(
                        input_device_name=input_name,
                        output_device_name=output_name,
                        sample_rate=48000,
                        buffer_size=512,
                    )
                    self.stream.__enter__()
                    self.stream.plugins = self.anonymizer.current_board()
                except Exception as exc:
                    messagebox.showerror("Errore", f"Impossibile avviare lo stream audio:\n{exc}")
                    self.stream = None
                    return
                self.start_button.config(text="Ferma")
                self.input_combo.config(state="disabled")
                self.output_combo.config(state="disabled")
            else:
                try:
                    self.stream.__exit__(None, None, None)
                finally:
                    self.stream = None
                self.start_button.config(text="Avvia")
                self.input_combo.config(state="readonly")
                self.output_combo.config(state="readonly")
            self._update_status()

        def _update_status(self):
            if self.stream is None:
                self.status_label.config(text="Stream non avviato", foreground="gray")
            else:
                stato = "ATTIVA (voce alterata)" if self.anonymizer.enabled else "DISATTIVATA (voce originale)"
                colore = "#2E7D32" if self.anonymizer.enabled else "#757575"
                self.status_label.config(text=f"Stream attivo — Alterazione: {stato}", foreground=colore)

        def _on_close(self):
            if self.stream is not None:
                try:
                    self.stream.__exit__(None, None, None)
                except Exception:
                    pass
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
                         help="Diagnostica dettagliata dei dispositivi audio (utile se un dispositivo non compare come input/output) ed esce")
    parser.add_argument("--input", type=str,
                         help="Nome del dispositivo di INPUT (il tuo microfono reale) — modalita' CLI")
    parser.add_argument("--output", type=str,
                         help="Nome del dispositivo di OUTPUT (il cavo/microfono virtuale) — modalita' CLI")
    parser.add_argument("--semitones", type=float, default=-4.0,
                         help="Pitch-shift in semitoni: negativo = voce piu' grave, positivo = piu' acuta (default: -4.0)")
    parser.add_argument("--chorus-mix", type=float, default=0.2,
                         help="Intensita' del chorus (0.0-1.0), aggiunge variazione timbrica (default: 0.2)")
    parser.add_argument("--samplerate", type=int, default=48000,
                         help="Frequenza di campionamento (default: 48000)")
    parser.add_argument("--buffer-size", type=int, default=512,
                         help="Dimensione del buffer audio; valori piu' bassi = meno latenza ma piu' rischio di glitch (default: 512)")
    parser.add_argument("--start-disabled", action="store_true",
                         help="Avvia con l'alterazione disattivata (bypass) — modalita' CLI")
    args = parser.parse_args()

    if args.diagnose:
        diagnose_devices()
        sys.exit(0)

    if args.list_devices:
        list_devices()
        sys.exit(0)

    # Se non vengono passati --input/--output espliciti, o viene richiesta
    # esplicitamente la GUI, si avvia l'interfaccia grafica.
    if args.gui or not (args.input and args.output):
        run_gui(default_semitones=args.semitones, default_chorus_mix=args.chorus_mix)
    else:
        run_cli(args)


if __name__ == "__main__":
    main()