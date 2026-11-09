"""The options the admin page can change. Each can still start from its environment variable; a saved value wins.
Options marked restart are read when the engine starts (`POST /admin/restart` or a service restart)."""

from settings import Setting

SETTINGS = [
    Setting("update_check_hours", "Check for updates every (hours)", "float", 6.0, "SERVICE_UPDATE_CHECK_HOURS", 0, 720,
            help="0 = only when you press Check now. Compares the Hub revisions with the last ones downloaded here"),
    Setting("max_inflight", "Calls allowed to wait for the model", "int", 0, "SERVICE_MAX_INFLIGHT", 0, 10000,
            help="Per service; more get 429. 0 = 4. Calls run one at a time"),
    Setting("max_body_mb", "Largest image (MB)", "int", 32, "SERVICE_MAX_BODY_MB", 1, 512,
            help="A bigger request gets 413"),
    Setting("device", "Device", "text", "auto", "SERVICE_DEVICE", restart=True, blank=False,
            help="auto = the GPU model on a Linux NVIDIA GPU with compute capability 8.0+ and enough memory, else the CPU model. "
                 "gpu = force the GPU model when any NVIDIA GPU is found. cpu = never use the GPU. Only the chosen model is downloaded"),
    Setting("min_vram_mb", "GPU memory needed for auto (MiB)", "int", 8192, "SERVICE_MIN_VRAM_MB", 1024, 1048576, restart=True,
            help="auto picks the GPU model only when the GPU has at least this much memory. The default is a guess; nothing has been measured"),
    Setting("gpu_mem_fraction", "GPU memory share for the model", "float", 0.7, "SERVICE_GPU_MEM_FRACTION", 0.1, 0.95, restart=True,
            help="vLLM gpu-memory-utilization. Lower it if the GPU is shared or loading runs out of memory"),
    Setting("warm", "Load the model with the service", "bool", True, "SERVICE_WARM", restart=True,
            help="Off = load it with POST /admin/restart. Needs the weights downloaded from the Updates card"),
]
