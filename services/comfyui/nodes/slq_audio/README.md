# slq_audio - SLQ Hub's own ComfyUI nodes for sound

Installed into the ComfyUI image (`services/comfyui/Dockerfile`) and queued only
by the Hub, through its GPU scheduler. Design: `docs/DESIGN.md` §8 "Voices and
remakes".

| Node | What |
|---|---|
| `SLQAceStepReference` | ACE-Step 1.5 with a **timbre** clip (heard by the timbre encoder only; the planner's codes stay the structure) and/or a **source** song (its semantic codes become the structure: a cover). ComfyUI's own `ReferenceTimbreAudio` uses one latent for both. |
| `SLQAceStepEncode` | `TextEncodeAceStepAudio1.5` without the planner, with bpm, key and time signature optional (`N/A` when unknown) - for covers. |
| `SLQSeparateVocals` | Vocals and accompaniment with a Mel-Band RoFormer vocal model, chunked and overlapped as MSST does. |
| `SLQSeedVC` | Seed-VC v1 voice conversion: speech (22.05 kHz) or singing with F0 (44.1 kHz), the reference trimmed to its voiced parts, whole-octave auto shift, loudness matched. Reports what it did as JSON text. |

Third-party code, loaded when a node runs (never at ComfyUI's start):

- **Seed-VC** (Plachtaa/seed-vc, GPL-3.0) at `SLQ_SEEDVC_DIR` (`/opt/seed-vc`),
  including its speaker-style weights `campplus_cn_common.bin`.
- **Music-Source-Separation-Training** (ZFTurbo, MIT) at `SLQ_MSST_DIR`
  (`/opt/msst`): `models/bs_roformer/{attend,mel_band_roformer}.py`, loaded
  under private module names, and the RoFormer configs.
- A Kaldi filterbank adapted from TorchAudio (BSD-2-Clause) in `seedvc.py`.

ComfyUI and Seed-VC are GPL-3.0, so this package is too. It runs only inside
the ComfyUI container; the Hub talks to ComfyUI over HTTP.

Model files (the Hub's catalog downloads them into ComfyUI's models folder):
`separation/MelBandRoformer.ckpt` (Kimberley Jensen's weights; no licence
stated), and under `seed-vc/` the two Seed-VC models, `whisper-small/`, the two
`bigvgan_v2_*` folders and `rmvpe.pt`.
