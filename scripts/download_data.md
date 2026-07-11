# Datasets

This project uses two public, de-identified datasets. No real PHI ever
enters the repo. The `data/` directory is gitignored.

## PriMock57 (primary, has audio)
57 mock primary care consultations with audio, transcripts, and
clinician notes. Drives the audio-in demo and latency numbers.

```
git clone https://github.com/babylonhealth/primock57 data/primock57
```
Check the repo license before any redistribution.

## ACI-Bench (scale, text)
207 doctor-patient dialogue and note pairs with expert-reviewed
references. Use for the note-structuring accuracy metric at larger N.
Obtain from the official ACI-Bench / MEDIQA-Chat release and place under
`data/aci-bench`. Check its license before redistribution.

## Held-out set discipline
Build a leak-free held-out labeled set that is never used to tune rules
or prompts. It is used only to score accuracy and drift. Report measured
numbers only.
