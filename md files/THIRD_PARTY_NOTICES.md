# Third-party notices

## Rare UI (https://rareui.com)
Components in `interface/app/src/components/rare/` are from Rare UI (https://github.com/swamimalode07/rare-ui). They are used unmodified, except for small adaptations (Next.js Link replaced with a button), under this license:

```
MIT + Commons Clause License Condition v1.0 + Attribution

Copyright (c) 2026 Swami Malode

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, and distribute the Software as part of an
application, website, or product, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

Attribution Requirement

Any project that ships any part of the Software must credit Rare UI with a
visible link to https://rareui.com, placed where a visitor or user can find it,
such as a site footer, an about page, a credits screen or a README. The credit
and the copyright notice above must not be removed from the source you copied.

Commons Clause Restriction

You may use this Software, including for any commercial purpose, so long as you
do not sell, sublicense, or redistribute the components themselves, whether
alone, in a bundle, or as a ported version.

No Warranty

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Chatterbox (https://github.com/resemble-ai/chatterbox)

Used as a library (the voice-conversion half, `chatterbox.vc`) to turn ISHTI's speech into the owner's own voice for
voice notes he sends; weights `ResembleAI/chatterbox` (s3gen.safetensors, conds.pt) in `data/clone`. MIT License,
Copyright (c) 2025 Resemble AI. Its package pins torch 2.6, which can't run on this GPU (RTX 5070 Ti, sm_120), so it is
installed with `pip install --no-deps chatterbox-tts` plus librosa, s3tokenizer, diffusers, resemble-perth,
conformer==0.3.2, omegaconf and einops; ISHTI keeps torch 2.11+cu128. Output carries Resemble's Perth watermark.
