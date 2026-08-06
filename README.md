# Voice Anonymizer

Alteratore vocale in tempo reale progettato per ridurre la riconoscibilità dell'impronta vocale durante riunioni, chiamate o registrazioni effettuate tramite applicazioni come Microsoft Teams, Zoom, Meet o qualsiasi altro software che utilizzi un microfono.

Il programma **non si collega direttamente alle applicazioni di conferenza**: acquisisce il segnale dal microfono reale, applica una catena di elaborazione audio in tempo reale e invia il risultato a un **microfono virtuale**, che deve poi essere selezionato come dispositivo di ingresso nell'applicazione desiderata.

Nota: al posto del microfono virtuale è sempre possibile utilizzare un loopback fisico.

Supportato:

* Windows
* Ubuntu/Linux

---

## Funzionamento

Pipeline audio:

```
Microfono reale
      |
      v
+-----------------------------+
| Stadio 1 - WORLD vocoder    |
| (pyworld, opzionale)        |
+-----------------------------+
      |
      v
+-----------------------------+
| Stadio 2 - Pedalboard DSP   |
+-----------------------------+
      |
      v
Microfono virtuale
(VB-CABLE / PulseAudio null sink)
      |
      v
Teams / Zoom / altre app
```

---

# Pipeline di anonimizzazione

## Stadio 1 - WORLD vocoder (pyworld)

Quando disponibile, il programma utilizza WORLD per modificare caratteristiche vocali più profonde rispetto a un semplice pitch shift.

Funzioni applicate:

* decomposizione del segnale in:

  * frequenza fondamentale (F0)
  * inviluppo spettrale
  * aperiodicità
* modifica controllata del pitch
* modifica dei formanti tramite vocal tract scaling
* tilt spettrale opzionale
* micro time-stretch opzionale
* randomizzazione dei parametri per sessione
* aggiunta opzionale di noise floor

La randomizzazione per-sessione permette di ottenere una trasformazione leggermente diversa ad ogni avvio del programma.

Esempio:

```
Avvio 1:
pitch = -2.4 semitoni
formant = 1.08

Avvio 2:
pitch = -2.7 semitoni
formant = 1.12
```

Questo evita che tutte le registrazioni abbiano esattamente la stessa trasformazione.

---

## Stadio 2 - Pedalboard DSP

Dopo WORLD viene applicata una catena di effetti audio:

* High Pass Filter

  * default: 150 Hz

* Low Pass Filter

  * default: 6000 Hz

* Compressor

  * threshold configurabile
  * ratio configurabile

* Chorus

  * variazioni timbriche leggere

* Reverb

  * simulazione ambiente

* Gain

  * regolazione volume finale

Quando WORLD è attivo, il `PitchShift` Pedalboard non viene utilizzato perché il pitch viene già modificato nello stadio WORLD.

---

# Modalità legacy

Se `pyworld` non è installato:

* il programma continua a funzionare
* viene mostrato un avviso
* viene utilizzata automaticamente la modalità legacy

Pipeline legacy:

```
Microfono
 |
 v
Pedalboard
 |
 +-- PitchShift
 +-- HighpassFilter
 +-- LowpassFilter
 +-- Compressor
 +-- Chorus
 +-- Reverb
 +-- Gain
 |
 v
Microfono virtuale
```

---

# Installazione

## Requisiti

* Python 3.10 o superiore consigliato
* dispositivo audio funzionante
* microfono virtuale

Esempi:

Windows:

* VB-CABLE
* Voicemeeter
* altri driver audio virtuali compatibili

Linux:

* PulseAudio null sink
* PipeWire virtual source

---

## Installazione dipendenze Python

Installare i pacchetti richiesti:

```bash
pip install pedalboard sounddevice numpy pyworld
```

`pyworld` è opzionale.

Se non disponibile:

```text
Il programma funziona comunque in modalità legacy.
```

---

## Tkinter

La GUI utilizza Tkinter.

Su molte installazioni Python è già incluso.

Ubuntu:

```bash
sudo apt install python3-tk
```

---

# Avvio programma

## Interfaccia grafica (consigliata)

```bash
python voice_anonymizer.py
```

oppure:

```bash
python voice_anonymizer.py --gui
```

La GUI permette di:

* selezionare microfono reale
* selezionare microfono virtuale
* attivare/disattivare anonimizzazione
* modificare parametri audio in tempo reale
* salvare configurazione
* monitorare livelli input/output

---

# Modalità comando

Esempio:

```bash
python voice_anonymizer.py \
 --input "Jabra" \
 --output "CABLE Input"
```

Durante l'esecuzione:

```
t + invio
```

Attiva/disattiva l'alterazione vocale.

```
q + invio
```

Chiude il programma.

---

# Diagnostica dispositivi

Elenco dispositivi:

```bash
python voice_anonymizer.py --list-devices
```

Diagnostica completa:

```bash
python voice_anonymizer.py --diagnose
```

Mostra:

* indice dispositivo
* canali input/output
* backend audio utilizzato
* sample rate predefinito

Controllo dispositivi predefiniti:

```bash
python voice_anonymizer.py --check-defaults
```

---

# Parametri CLI disponibili

## Audio

| Parametro       | Default | Descrizione             |
| --------------- | ------- | ----------------------- |
| `--samplerate`  | 48000   | Frequenza campionamento |
| `--buffer-size` | 1024    | Dimensione buffer audio |
| `--chorus-mix`  | 0.15    | Intensità chorus        |

---

## WORLD / anonimizzazione

| Parametro             | Default | Descrizione              |
| --------------------- | ------- | ------------------------ |
| `--semitones`         | -2.5    | Modifica pitch           |
| `--formant-scale`     | 1.10    | Scala formanti           |
| `--pitch-variation`   | 0.3     | Randomizzazione pitch    |
| `--formant-variation` | 0.03    | Randomizzazione formanti |
| `--spectral-tilt`     | 0.0     | Tilt spettrale           |
| `--time-scale`        | 1.0     | Micro time-stretch       |
| `--noise-floor`       | -55     | Noise floor dBFS         |

Disabilitare randomizzazione:

```bash
--no-randomize
```

Avviare con bypass:

```bash
--start-disabled
```

---

# Configurazione salvata

La GUI salva automaticamente le impostazioni nel file:

```
voice_anonymizer_config.json
```

Vengono salvati:

* dispositivi audio scelti
* pitch
* formant scale
* chorus
* reverb
* gain
* filtri
* compressore
* stato WORLD

---

# Interfaccia grafica

La GUI include:

## Selezione dispositivi

* microfono input
* dispositivo virtuale output
* rilevamento automatico dispositivi

## Controlli anonimizzazione

* WORLD vocoder ON/OFF
* Pitch shift
* Formant scale
* Chorus
* Riverbero

## Controlli DSP

* filtro passa-alto
* filtro passa-basso
* compressore
* gain output

## Monitoraggio

Indicatori live:

* livello microfono reale
* livello uscita virtuale

---

# Collegamento con Microsoft Teams

Procedura:

1. Avviare Voice Anonymizer.
2. Selezionare:

   * input = microfono reale
   * output = microfono virtuale.
3. Avviare lo stream.
4. Aprire Teams.
5. Andare nelle impostazioni audio.
6. Selezionare come microfono:

```
Microfono virtuale
```

Teams riceverà il segnale già modificato.

---

# Risoluzione problemi

## Il microfono non compare

Windows:

* verificare che il microfono sia abilitato nelle impostazioni audio
* evitare modalità esclusiva occupata da altre applicazioni
* preferire backend WASAPI quando disponibile

Linux:

verificare:

```bash
pactl list sources short
```

oppure usare:

```bash
pavucontrol
```

---

## Bluetooth senza microfono

Alcuni dispositivi Bluetooth espongono:

* profilo A2DP:

  * qualità audio alta
  * nessun microfono

* profilo Headset/HFP:

  * microfono disponibile

Se il microfono non appare, controllare il profilo Bluetooth.

---

## WORLD genera errori

Il programma protegge lo stream:

* blocchi troppo corti vengono ignorati
* silenzi vengono bypassati
* errori WORLD causano fallback al segnale originale

Lo stream audio continua senza interrompersi.

---

# Architettura software

Componenti principali:

```
voice_anonymizer.py

 |
 +-- VoicePrivacyConfig
 |      gestione parametri WORLD
 |
 +-- world_process()
 |      vocoder WORLD
 |
 +-- VoiceAnonymizer
 |      gestione pipeline DSP
 |
 +-- AudioEngine
 |      stream full duplex sounddevice
 |
 +-- GUI Tkinter
 |
 +-- CLI
```

---

# Licenza

GPL-3.0 license