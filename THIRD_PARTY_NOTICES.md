# Third-Party Notices

ChatVoice Local's own source code is released under the MIT License (see `LICENSE`).

This project uses or can download the third-party components listed below. Each one stays under its own license. Nothing in this file changes those licenses.

---

## Speech engines and models

### Kokoro-82M (voices)
- Use: text-to-speech voices
- License: Apache License 2.0
- Source: https://huggingface.co/hexgrad/Kokoro-82M
- License text: https://www.apache.org/licenses/LICENSE-2.0

### misaki (text-to-phoneme helper)
- Use: converts text to phonemes for Kokoro
- License: Apache License 2.0
- Source: https://github.com/hexgrad/misaki
- License text: https://www.apache.org/licenses/LICENSE-2.0

### Qwen3-TTS (voice cloning)
- Use: voice cloning and fine-tuned voices
- License: Apache License 2.0 (tokenizers and models)
- Source: https://github.com/QwenLM/Qwen3-TTS
- License text: https://www.apache.org/licenses/LICENSE-2.0

---

## espeak-ng (GPL-3.0-or-later)

- Use: fallback pronunciation for words misaki does not know. It is loaded through the `espeakng_loader` package.
- License: GNU General Public License v3 (GPLv3)
- Source code: https://github.com/espeak-ng/espeak-ng
- License text: https://www.gnu.org/licenses/gpl-3.0.txt

**If you only use the source code from GitHub:** espeak-ng is not included in this repository. It is installed separately through the Python packages listed in the requirements file.

**If you use a built installer or `.exe`:** the build includes the espeak-ng library, which is GPLv3 software. A full copy of the GPLv3 license text is shipped with the installer in `licenses/GPL-3.0.txt`. The source code of espeak-ng is available at the link above. espeak-ng is a separate program that is only loaded at runtime. It is not owned or modified by this project.

---

## Python packages

| Package | Use | License (as far as known) |
|---|---|---|
| Pillow | images and the UI | HPND (permissive) |
| websockets | chat connections | BSD-3-Clause |
| sounddevice | audio output | MIT |
| soundfile | reading and writing audio files | BSD-3-Clause |
| keyring | storing login tokens securely | MIT |
| espeakng_loader | loads the espeak-ng library | see the espeak-ng section above |

> Please check the license of each package again before every release. Package licenses can change between versions.

---

## Online services

ChatVoice Local connects to the following services at runtime. It does not include or resell their content.

- **Twitch** and **Kick**: chat, events and moderation, through their official APIs. Use is subject to each platform's developer terms and terms of service.
- **7TV**, **BetterTTV** and **FrankerFaceZ**: emotes are fetched at runtime and cached on the user's own PC. No emote images are shipped with this project. Use of each service is subject to its own terms of service.

Twitch, Kick, 7TV, BetterTTV, FrankerFaceZ and Kokoro are names and trademarks of their respective owners. This project is not affiliated with or endorsed by any of them.

---

*This file is provided for information and is not legal advice.*
