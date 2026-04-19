export const wsUrl =
  import.meta.env.VITE_AUNIC_WS_URL ??
  `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`;
