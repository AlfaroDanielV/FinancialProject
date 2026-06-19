import axios, { AxiosError, AxiosInstance, InternalAxiosRequestConfig } from "axios";

import { env } from "../lib/env";
import { clearSession, getSessionToken } from "../lib/auth";

type UnauthorizedHandler = () => void;
let unauthorizedHandler: UnauthorizedHandler | null = null;

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null) {
  unauthorizedHandler = handler;
}

function attachAuthHeader(config: InternalAxiosRequestConfig): InternalAxiosRequestConfig {
  const token = getSessionToken();
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
  }
  return config;
}

async function handleResponseError(error: AxiosError): Promise<never> {
  if (error.response?.status === 401) {
    await clearSession();
    unauthorizedHandler?.();
  }
  return Promise.reject(error);
}

function buildClient(): AxiosInstance {
  const instance = axios.create({
    baseURL: `${env.apiBaseUrl}${env.apiPrefix}`,
    timeout: 15000,
    headers: {
      Accept: "application/json",
    },
  });
  instance.interceptors.request.use(attachAuthHeader);
  instance.interceptors.response.use((response) => response, handleResponseError);
  return instance;
}

export const api = buildClient();
