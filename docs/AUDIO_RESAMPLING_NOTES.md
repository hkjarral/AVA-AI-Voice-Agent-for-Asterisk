# Audio Resampling — Important Notes

## Why `audioop.ratecv()` Was Replaced

The built-in Python `audioop.ratecv()` (C implementation) was the root cause of
audible **crackling / static noise** in real-time telephony audio. Three distinct
issues were identified:

### 1. Incorrect Output Size

`audioop.ratecv()` produces off-by-one sample counts for common rate conversions.

| Input (8 kHz, 20 ms) | Expected (16 kHz) | Actual |
|---|---|---|
| 320 bytes (160 samples) | 640 bytes (320 samples) | **638 bytes (319 samples)** |

This 2-byte misalignment accumulates across chunks and corrupts the downstream
audio stream. A pad/trim workaround was previously used in `engine.py` to force
exact sizes, but zero-padding itself introduces audible clicks.

### 2. Missing State Across Chunks

Many call sites were invoking `audioop.ratecv(..., state=None)`, meaning each
20 ms chunk was resampled **independently** with no knowledge of the previous
chunk. This creates a **sample discontinuity** at every chunk boundary:

```
Chunk A: [..., 1200, 1350]   (last sample = 1350)
Chunk B: [1400, 1450, ...]   (first sample = 1400, no interpolation from 1350)
                       ↑
               audible "click" here
```

At 50 chunks per second (20 ms each), these discontinuities manifest as a
persistent **crackling / buzzing sound** (~50 Hz fundamental).

### 3. Integer Arithmetic Precision Drift

Even when state **was** passed correctly, the C implementation uses integer
arithmetic internally. Over many consecutive chunks, small rounding errors
accumulate, causing a gradual **phase drift** between the expected and actual
sample positions — heard as a low-frequency buzz.

---

## The Fix: NumPy Linear Interpolation with Exact Positioning

The replacement in `src/audio/resampler.py` uses:

```python
step = float(n_in) / float(n_out)            # exact ratio (e.g. 0.5 for 2×)
out_pos = np.arange(n_out) * step             # mathematically exact positions
resampled = np.interp(out_pos, ..., audio)    # interpolate at those positions
```

### Key design decisions:

1. **`arange * step` instead of `linspace`**
   - `np.linspace(0, n_in, n_out)` computes step as `n_in / (n_out - 1)`,
     which for 160 → 320 gives step = `0.50156...` instead of `0.5`.
   - This ~0.3% error means a **0.5-sample skip** at every chunk boundary,
     producing a subtle 50 Hz buzz.
   - `np.arange(n_out) * step` preserves the exact ratio with full float64
     precision — no drift, no buzz.

2. **1-sample state carry**
   - The last input sample of each chunk is stored in `state = (float(audio[-1]),)`.
   - On the next chunk, this sample is **prepended** to the input array, and
     output positions are shifted by one step (`arange(1, n_out+1) * step`).
   - This means the first output sample of Chunk B is **interpolated between**
     the last sample of Chunk A and the first sample of Chunk B — perfectly
     smooth boundary, zero discontinuity.

   ```
   Chunk A: [..., 1200, 1350]     state = (1350.0,)
                            ↘
   Chunk B: [1350, 1400, ...]     extended = [1350, 1400, ...]
                  ↑                first output ≈ 1375 (smooth!)
                  from state
   ```

3. **Exact output size**
   - `n_out = int(round(n_in * target_rate / source_rate))`
   - Output is always exactly `n_out * 2` bytes — no padding or trimming needed.

---

## Where the Fix Is Applied

All `audioop.ratecv()` calls in `src/` were replaced with
`resample_audio()` from `src/audio/resampler.py`:

| File | Calls replaced | Notes |
|---|---|---|
| `src/engine.py` | 8 | Pad/trim workaround removed |
| `src/rtp_server.py` | 1 | Stateful (per-session) |
| `src/providers/elevenlabs_agent.py` | 2 | Input + output paths |
| `src/providers/local.py` | 2 | Stateless (batch mode) |
| `src/pipelines/local.py` | 2 | STT format conversion |
| `src/pipelines/elevenlabs.py` | 2 | TTS output conversion |
| `src/pipelines/deepgram.py` | 1 | STT input resampling |

**Not replaced:**
- `admin_ui/` — Separate Docker service, does not perform real-time streaming.
  Uses `audioop` only for offline file conversion where chunk boundaries are
  not an issue.

---

## Dependencies

- **`numpy>=1.24.0`** — Required for `np.interp`, `np.arange`, `np.frombuffer`.
  Added to `requirements.txt`.
- **`audioop`** — Still used for μ-law/a-law codec conversions (`ulaw2lin`,
  `lin2ulaw`, `alaw2lin`, `lin2alaw`) which are unaffected by the resampling
  issues described above.
