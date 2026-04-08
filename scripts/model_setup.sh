#!/usr/bin/env bash
set -euo pipefail

# Simple Bash-based model setup (no Python required)
# - Detects tier from CPU cores and RAM
# - Downloads STT/LLM/TTS artifacts for LIGHT|MEDIUM|HEAVY
# - Uses curl and unzip (install unzip if missing)
# Paths mirror models/registry.json

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)"
MODELS_DIR="${ROOT_DIR}/models"
ASSUME_YES=0
TIER_OVERRIDE=""
LANGUAGE="en-US"

usage() {
  cat <<EOF
Usage: $0 [--tier LIGHT|MEDIUM|HEAVY] [--assume-yes] [--language LANG_CODE]

Downloads local provider models under models/ and prints expected performance.

Options:
  --tier         Force a specific tier (LIGHT, MEDIUM, HEAVY)
  --assume-yes   Skip confirmation prompts
  --language     Language code for STT/TTS models (default: en-US)
                 Supported: en-US, es-ES, fr-FR, de-DE, it-IT, pt-BR, nl-NL,
                           ru-RU, pl-PL, zh-CN, ja-JP, ko-KR, hi-IN, ar, tr-TR
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --assume-yes) ASSUME_YES=1; shift ;;
    --tier) TIER_OVERRIDE="${2:-}"; shift 2 ;;
    --language) LANGUAGE="${2:-en-US}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) shift ;;
  esac
done

echo "🌍 Language: ${LANGUAGE}"

# Language-to-model URL mappings
get_vosk_model_url() {
  case "$LANGUAGE" in
    en-US) echo "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip|vosk-model-small-en-us-0.15" ;;
    es-ES) echo "https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip|vosk-model-small-es-0.42" ;;
    fr-FR) echo "https://alphacephei.com/vosk/models/vosk-model-small-fr-0.22.zip|vosk-model-small-fr-0.22" ;;
    de-DE) echo "https://alphacephei.com/vosk/models/vosk-model-small-de-0.15.zip|vosk-model-small-de-0.15" ;;
    it-IT) echo "https://alphacephei.com/vosk/models/vosk-model-small-it-0.22.zip|vosk-model-small-it-0.22" ;;
    pt-BR) echo "https://alphacephei.com/vosk/models/vosk-model-small-pt-0.3.zip|vosk-model-small-pt-0.3" ;;
    nl-NL) echo "https://alphacephei.com/vosk/models/vosk-model-small-nl-0.22.zip|vosk-model-small-nl-0.22" ;;
    ru-RU) echo "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip|vosk-model-small-ru-0.22" ;;
    pl-PL) echo "https://alphacephei.com/vosk/models/vosk-model-small-pl-0.22.zip|vosk-model-small-pl-0.22" ;;
    zh-CN) echo "https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip|vosk-model-small-cn-0.22" ;;
    ja-JP) echo "https://alphacephei.com/vosk/models/vosk-model-small-ja-0.22.zip|vosk-model-small-ja-0.22" ;;
    ko-KR) echo "https://alphacephei.com/vosk/models/vosk-model-small-ko-0.22.zip|vosk-model-small-ko-0.22" ;;
    hi-IN) echo "https://alphacephei.com/vosk/models/vosk-model-small-hi-0.22.zip|vosk-model-small-hi-0.22" ;;
    ar)    echo "https://alphacephei.com/vosk/models/vosk-model-ar-0.22-linto-1.1.0.zip|vosk-model-ar-0.22-linto-1.1.0" ;;
    tr-TR) echo "https://alphacephei.com/vosk/models/vosk-model-small-tr-0.3.zip|vosk-model-small-tr-0.3" ;;
    *)     echo "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip|vosk-model-small-en-us-0.15" ;;
  esac
}

get_piper_model_url() {
  case "$LANGUAGE" in
    en-US) echo "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx|en_US-lessac-medium.onnx" ;;
    es-ES) echo "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx|es_ES-davefx-medium.onnx" ;;
    fr-FR) echo "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx|fr_FR-siwis-medium.onnx" ;;
    de-DE) echo "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx|de_DE-thorsten-medium.onnx" ;;
    it-IT) echo "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/it/it_IT/riccardo/x_low/it_IT-riccardo-x_low.onnx|it_IT-riccardo-x_low.onnx" ;;
    pt-BR) echo "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx|pt_BR-faber-medium.onnx" ;;
    nl-NL) echo "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/nl/nl_NL/mls/medium/nl_NL-mls-medium.onnx|nl_NL-mls-medium.onnx" ;;
    ru-RU) echo "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx|ru_RU-irina-medium.onnx" ;;
    pl-PL) echo "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/pl/pl_PL/mls/medium/pl_PL-mls-medium.onnx|pl_PL-mls-medium.onnx" ;;
    zh-CN) echo "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx|zh_CN-huayan-medium.onnx" ;;
    ja-JP) echo "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx|en_US-lessac-medium.onnx" ;; # Piper doesn't have Japanese
    ko-KR) echo "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx|en_US-lessac-medium.onnx" ;; # Piper doesn't have Korean
    hi-IN) echo "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx|en_US-lessac-medium.onnx" ;; # Fallback
    ar)    echo "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ar/ar_JO/kareem/medium/ar_JO-kareem-medium.onnx|ar_JO-kareem-medium.onnx" ;;
    tr-TR) echo "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/tr/tr_TR/dfki/medium/tr_TR-dfki-medium.onnx|tr_TR-dfki-medium.onnx" ;;
    *)     echo "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx|en_US-lessac-medium.onnx" ;;
  esac
}

# Parse model info from functions
VOSK_INFO=$(get_vosk_model_url)
VOSK_URL="${VOSK_INFO%|*}"
VOSK_MODEL="${VOSK_INFO#*|}"

PIPER_INFO=$(get_piper_model_url)
PIPER_URL="${PIPER_INFO%|*}"
PIPER_MODEL="${PIPER_INFO#*|}"

echo "📦 Vosk model: ${VOSK_MODEL}"
echo "🔊 Piper voice: ${PIPER_MODEL}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "ERROR: '$1' is required" >&2; exit 1; }
}

need_cmd curl
if ! command -v unzip >/dev/null 2>&1; then
  echo "WARNING: 'unzip' not found; STT model extraction may fail. Install with: apt-get install -y unzip" >&2
fi

confirm() { # confirm "message"
  if [ "$ASSUME_YES" -eq 1 ]; then return 0; fi
  read -r -p "$1 [y/N]: " ans
  case "$ans" in
    y|Y|yes|YES) return 0;;
    *) return 1;;
  esac
}

cpu_cores() {
  if command -v nproc >/dev/null 2>&1; then nproc; else getconf _NPROCESSORS_ONLN || echo 1; fi
}

ram_gb() {
  if [ -r /proc/meminfo ]; then
    awk '/MemTotal:/ { printf "%d\n", $2/1024/1024 }' /proc/meminfo
  elif command -v sysctl >/dev/null 2>&1; then
    sysctl -n hw.memsize 2>/dev/null | awk '{ printf "%d\n", $1/1024/1024/1024 }'
  else
    echo 0
  fi
}

detect_gpu() {
  # Check NVIDIA GPU
  if command -v nvidia-smi >/dev/null 2>&1; then
    if nvidia-smi >/dev/null 2>&1; then
      echo 1
      return
    fi
  fi
  # Check AMD GPU
  if command -v rocm-smi >/dev/null 2>&1; then
    if rocm-smi >/dev/null 2>&1; then
      echo 1
      return
    fi
  fi
  echo 0
}

benchmark_cpu() {
  # Quick CPU benchmark using simple computation (no model needed)
  # Returns score: 1.0 = old CPU, 3.0 = mid-range, 5.0+ = modern high-end
  local start end elapsed score
  
  # Time a simple CPU-intensive task
  start=$(date +%s%N 2>/dev/null || echo 0)
  if [ "$start" -eq 0 ]; then
    # Fallback if nanoseconds not available
    echo "2.5"
    return
  fi
  
  # CPU benchmark: calculate primes (simple but CPU-intensive)
  awk 'BEGIN {
    count = 0
    for (i = 2; i <= 50000; i++) {
      is_prime = 1
      for (j = 2; j * j <= i; j++) {
        if (i % j == 0) {
          is_prime = 0
          break
        }
      }
      if (is_prime) count++
    }
  }' >/dev/null 2>&1
  
  end=$(date +%s%N)
  elapsed=$(( (end - start) / 1000000 ))  # Convert to milliseconds
  
  # Score based on time (faster = higher score)
  # Baseline: 5000ms = score 1.0, 1000ms = score 5.0
  if [ "$elapsed" -gt 0 ]; then
    score=$(awk -v t="$elapsed" 'BEGIN { printf "%.1f\n", 5000.0 / t }')
  else
    score="2.5"
  fi
  
  echo "$score"
}

select_tier() {
  local cores ram gpu cpu_score
  cores=$(cpu_cores)
  ram=$(ram_gb)
  gpu=$(detect_gpu)
  
  if [ -n "$TIER_OVERRIDE" ]; then echo "$TIER_OVERRIDE"; return; fi
  
  # GPU-accelerated tiers
  if [ "$gpu" -eq 1 ]; then
    if [ "$ram" -ge 32 ] && [ "$cores" -ge 8 ]; then echo HEAVY_GPU; return; fi
    if [ "$ram" -ge 16 ] && [ "$cores" -ge 4 ]; then echo MEDIUM_GPU; return; fi
    echo LIGHT_CPU; return  # GPU but low resources, use CPU tier
  fi
  
  # CPU-only tiers - benchmark CPU performance
  echo "Benchmarking CPU performance..." >&2
  cpu_score=$(benchmark_cpu)
  echo "CPU benchmark score: $cpu_score (higher is better)" >&2
  
  # Select tier based on resources AND performance
  if [ "$ram" -ge 32 ] && [ "$cores" -ge 16 ]; then
    # Check if CPU is fast enough for HEAVY_CPU
    if awk -v score="$cpu_score" 'BEGIN { exit !(score >= 4.0) }'; then
      echo HEAVY_CPU
      return
    else
      echo "⚠️  CPU performance too low for HEAVY_CPU tier (score: $cpu_score < 4.0)" >&2
      echo "   Falling back to MEDIUM_CPU for better reliability" >&2
      echo MEDIUM_CPU
      return
    fi
  fi
  
  if [ "$ram" -ge 16 ] && [ "$cores" -ge 8 ]; then
    echo MEDIUM_CPU
    return
  fi
  
  if [ "$ram" -ge 8 ] && [ "$cores" -ge 4 ]; then
    echo LIGHT_CPU
    return
  fi
  
  echo LIGHT_CPU
}

download() { # url dest_path label
  local url="$1" dest="$2" label="$3"
  mkdir -p "$(dirname "$dest")"
  echo "Downloading $label → $dest"
  curl -L --retry 3 --fail -o "$dest" "$url"
}

extract_zip() { # zip_path target_dir
  local zip_path="$1" target_dir="$2"
  local target_name="$(basename "$target_dir")"
  if command -v unzip >/dev/null 2>&1; then
    echo "Extracting $(basename "$zip_path") → $target_dir"
    rm -rf "$target_dir"
    mkdir -p "$target_dir"
    unzip -q -o "$zip_path" -d "$target_dir"
    # Fix nested directory if zip contained a folder with same name
    if [ -d "$target_dir/$target_name" ] && [ ! -f "$target_dir/README" ]; then
      mv "$target_dir/$target_name"/* "$target_dir/" 2>/dev/null || true
      rmdir "$target_dir/$target_name" 2>/dev/null || true
    fi
  else
    echo "ERROR: unzip not found. Please install unzip and re-run." >&2
    exit 1
  fi
}

setup_light_cpu() {
  # STT (Vosk) - language-aware model
  local stt_zip="$MODELS_DIR/stt/${VOSK_MODEL}.zip"
  if [ ! -f "$MODELS_DIR/stt/${VOSK_MODEL}/README" ]; then
    download "${VOSK_URL}" "$stt_zip" "${VOSK_MODEL}"
    extract_zip "$stt_zip" "$MODELS_DIR/stt/${VOSK_MODEL}"
    rm -f "$stt_zip"
  else
    echo "STT model already exists, skipping download"
  fi
  # LLM (Qwen 2.5-1.5B Instruct) - best CPU voice model, fast inference + tool calling
  # Falls back to TinyLlama if download fails
  local qwen_dst="$MODELS_DIR/llm/qwen2.5-1.5b-instruct-q4_k_m.gguf"
  if [ ! -f "$qwen_dst" ]; then
    if ! download "https://huggingface.co/bartowski/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf" \
                   "$qwen_dst" "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"; then
      echo "Qwen 2.5-1.5B download failed, falling back to TinyLlama..." >&2
      local tiny_dst="$MODELS_DIR/llm/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf"
      download "https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF/resolve/main/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf" \
               "$tiny_dst" "tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf"
    fi
  else
    echo "LLM model already exists, skipping download"
  fi
  # TTS (Piper) - language-aware voice
  download "${PIPER_URL}" "$MODELS_DIR/tts/${PIPER_MODEL}" "${PIPER_MODEL}"
  download "${PIPER_URL}.json" "$MODELS_DIR/tts/${PIPER_MODEL}.json" "${PIPER_MODEL}.json"
}

setup_medium_cpu() {
  # STT (Vosk) - language-aware model
  local stt_zip="$MODELS_DIR/stt/${VOSK_MODEL}.zip"
  if [ ! -f "$MODELS_DIR/stt/${VOSK_MODEL}/README" ]; then
    download "${VOSK_URL}" "$stt_zip" "${VOSK_MODEL}"
    extract_zip "$stt_zip" "$MODELS_DIR/stt/${VOSK_MODEL}"
    rm -f "$stt_zip"
  else
    echo "STT model already exists, skipping download"
  fi
  # LLM (Qwen 2.5-1.5B Instruct - much faster than Phi-3 on CPU, good tool calling)
  local qwen_dst="$MODELS_DIR/llm/qwen2.5-1.5b-instruct-q4_k_m.gguf"
  if [ ! -f "$qwen_dst" ]; then
    if ! download "https://huggingface.co/bartowski/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf" \
                   "$qwen_dst" "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"; then
      echo "Qwen 2.5-1.5B download failed, falling back to Phi-3..." >&2
      download "https://huggingface.co/microsoft/Phi-3-mini-4k-instruct-gguf/resolve/main/Phi-3-mini-4k-instruct-q4.gguf" \
               "$MODELS_DIR/llm/phi-3-mini-4k-instruct.Q4_K_M.gguf" "phi-3-mini-4k-instruct.Q4_K_M.gguf"
    fi
  else
    echo "LLM model already exists, skipping download"
  fi
  # TTS (Piper) - language-aware voice
  download "${PIPER_URL}" "$MODELS_DIR/tts/${PIPER_MODEL}" "${PIPER_MODEL}"
  download "${PIPER_URL}.json" "$MODELS_DIR/tts/${PIPER_MODEL}.json" "${PIPER_MODEL}.json"
}

setup_heavy_cpu() {
  # Use Phi-3-mini by default for better out-of-box experience
  # Llama-2-7B often too slow on older CPUs despite high core count
  echo "⚠️  Note: HEAVY_CPU tier uses Phi-3-mini (3.8B) for reliability."
  echo "   Llama-2-7B requires modern CPUs with AVX-512 and often times out."
  echo "   Phi-3-mini provides 90% of the quality at 50% of the latency."
  echo ""
  
  # Reuse MEDIUM_CPU setup (Phi-3-mini)
  setup_medium_cpu
}

setup_medium_gpu() {
  # Same as MEDIUM_CPU but with note about GPU usage
  setup_heavy_cpu  # Use Llama-2-7B for GPU tiers too
}

setup_heavy_gpu() {
  # STT (Vosk) - language-aware model
  local stt_zip="$MODELS_DIR/stt/${VOSK_MODEL}.zip"
  if [ ! -f "$MODELS_DIR/stt/${VOSK_MODEL}/README" ]; then
    download "${VOSK_URL}" "$stt_zip" "${VOSK_MODEL}"
    extract_zip "$stt_zip" "$MODELS_DIR/stt/${VOSK_MODEL}"
    rm -f "$stt_zip"
  else
    echo "STT model already exists, skipping download"
  fi
  # LLM (Llama-2 13B - only for GPU)
  if [ ! -f "$MODELS_DIR/llm/llama-2-13b-chat.Q4_K_M.gguf" ]; then
    download "https://huggingface.co/TheBloke/Llama-2-13B-chat-GGUF/resolve/main/llama-2-13b-chat.Q4_K_M.gguf" \
             "$MODELS_DIR/llm/llama-2-13b-chat.Q4_K_M.gguf" "llama-2-13b-chat.Q4_K_M.gguf"
  else
    echo "LLM model already exists, skipping download"
  fi
  # TTS (Piper) - language-aware voice
  download "${PIPER_URL}" "$MODELS_DIR/tts/${PIPER_MODEL}" "${PIPER_MODEL}"
  download "${PIPER_URL}.json" "$MODELS_DIR/tts/${PIPER_MODEL}.json" "${PIPER_MODEL}.json"
}

main() {
  mkdir -p "$MODELS_DIR"/stt "$MODELS_DIR"/llm "$MODELS_DIR"/tts
  local tier gpu_status
  gpu_status=$(detect_gpu)
  tier="$(select_tier)"
  
  echo "=== System detection (bash) ==="
  echo "CPU cores: $(cpu_cores)"
  echo "Total RAM: $(ram_gb) GB"
  echo "GPU detected: $([ "$gpu_status" -eq 1 ] && echo 'Yes' || echo 'No')"
  echo "Selected tier: ${tier}${TIER_OVERRIDE:+ (override)}"
  echo ""
  
  # Show expected performance and recommended settings
  case "$tier" in
    LIGHT_CPU)
      echo "Expected performance: 25-40 seconds per conversational turn (TinyLlama)"
      echo "Note: Slower response time due to CPU limitations, but reliable"
      echo "Recommended .env settings:"
      echo "  LOCAL_LLM_CONTEXT=512"
      echo "  LOCAL_LLM_MAX_TOKENS=24"
      echo "  LOCAL_LLM_INFER_TIMEOUT_SEC=45"
      ;;
    MEDIUM_CPU)
      echo "Expected performance: 20-30 seconds per conversational turn (Phi-3-mini)"
      echo "Note: Optimized for CPU-only environments"
      echo "Recommended .env settings:"
      echo "  LOCAL_LLM_CONTEXT=512"
      echo "  LOCAL_LLM_MAX_TOKENS=32"
      echo "  LOCAL_LLM_INFER_TIMEOUT_SEC=30"
      ;;
    HEAVY_CPU)
      echo "Expected performance: 25-35 seconds per conversational turn (Phi-3-mini)"
      echo "Note: Now uses Phi-3-mini instead of Llama-2-7B for reliability"
      echo "Recommended .env settings:"
      echo "  LOCAL_LLM_CONTEXT=512"
      echo "  LOCAL_LLM_MAX_TOKENS=28"
      echo "  LOCAL_LLM_INFER_TIMEOUT_SEC=35"
      ;;
    MEDIUM_GPU)
      echo "Expected performance: 8-12 seconds per conversational turn (GPU-accelerated)"
      echo "Recommended .env settings:"
      echo "  LOCAL_LLM_CONTEXT=1024"
      echo "  LOCAL_LLM_MAX_TOKENS=48"
      echo "  LOCAL_LLM_INFER_TIMEOUT_SEC=20"
      ;;
    HEAVY_GPU)
      echo "Expected performance: 10-15 seconds per conversational turn (GPU-accelerated)"
      echo "Recommended .env settings:"
      echo "  LOCAL_LLM_CONTEXT=1024"
      echo "  LOCAL_LLM_MAX_TOKENS=48"
      echo "  LOCAL_LLM_INFER_TIMEOUT_SEC=20"
      ;;
  esac
  echo ""
  
  if ! confirm "Proceed with model download/setup?"; then
    echo "Aborted by user."; exit 0
  fi
  
  case "$tier" in
    LIGHT_CPU) setup_light_cpu ;;
    MEDIUM_CPU) setup_medium_cpu ;;
    HEAVY_CPU) setup_heavy_cpu ;;
    MEDIUM_GPU) setup_medium_gpu ;;
    HEAVY_GPU) setup_heavy_gpu ;;
    *) echo "Unknown tier: $tier"; exit 1 ;;
  esac
  
  echo ""
  echo "✅ Models ready under $MODELS_DIR."
  echo "Next steps:"
  echo "1. Update .env to point to downloaded models (or run install.sh autodetect)"
  echo "2. Start services: docker-compose up -d"
  echo "3. Place test call to verify performance"
}

main "$@"
