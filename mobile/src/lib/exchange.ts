import axios from "axios";

import { env } from "./env";
import { setSession } from "./auth";

interface ExchangeResponse {
  user_id: string;
  email: string;
  full_name: string;
  token: string;
  expires_at: number;
}

export class ExchangeError extends Error {
  constructor(message: string, public readonly cause?: unknown) {
    super(message);
    this.name = "ExchangeError";
  }
}

async function postExchange(
  path: string,
  body: Record<string, string>,
  rejectMessage: string,
): Promise<ExchangeResponse> {
  try {
    const response = await axios.post<ExchangeResponse>(
      `${env.apiBaseUrl}${env.apiPrefix}${path}`,
      body,
      {
        timeout: 15000,
        headers: { "Content-Type": "application/json" },
      },
    );
    const data = response.data;
    await setSession(data.token, data.expires_at);
    return data;
  } catch (err) {
    if (axios.isAxiosError(err) && err.response?.status === 401) {
      throw new ExchangeError(rejectMessage, err);
    }
    throw new ExchangeError(
      "No pude conectarme al servidor. Revisá tu conexión y volvé a intentar.",
      err,
    );
  }
}

export async function exchangeMagicLink(rawToken: string): Promise<ExchangeResponse> {
  if (!rawToken || !rawToken.includes(".")) {
    throw new ExchangeError("Link inválido: falta el token.");
  }
  return postExchange(
    "/auth/magic-link/exchange",
    { token: rawToken },
    "Link expirado o ya usado. Pedile uno nuevo con /setup en Telegram.",
  );
}

export async function exchangeDeviceCode(code: string): Promise<ExchangeResponse> {
  const normalized = code.trim().toUpperCase();
  if (!normalized) {
    throw new ExchangeError("Falta el código.");
  }
  return postExchange(
    "/auth/device-code/exchange",
    { code: normalized },
    "Código inválido o vencido. Pedile uno nuevo con /login en Telegram.",
  );
}
