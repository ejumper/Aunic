type EnvLike = {
  DEV: boolean;
  VITE_AUNIC_WS_URL?: string;
  VITE_AUNIC_ALLOW_PROD_WS_URL?: string;
};

type LocationLike = Pick<Location, "protocol" | "host">;

export function sameOriginWsUrl(locationLike: LocationLike): string {
  return `${locationLike.protocol === "https:" ? "wss:" : "ws:"}//${locationLike.host}/ws`;
}

export function resolveWsUrl(env: EnvLike, locationLike: LocationLike): string {
  const fallback = sameOriginWsUrl(locationLike);
  if (!env.VITE_AUNIC_WS_URL) {
    return fallback;
  }
  if (env.DEV || env.VITE_AUNIC_ALLOW_PROD_WS_URL === "true") {
    return env.VITE_AUNIC_WS_URL;
  }
  return fallback;
}

const sameOriginUrl = sameOriginWsUrl(location);
const allowProdOverride = import.meta.env.VITE_AUNIC_ALLOW_PROD_WS_URL === "true";

export const wsUrl =
  import.meta.env.DEV || allowProdOverride
    ? import.meta.env.VITE_AUNIC_WS_URL ?? sameOriginUrl
    : sameOriginUrl;
