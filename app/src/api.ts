import { appConfig } from './config';
import { getIdentity } from './identity';
export class ApiError extends Error { constructor(message: string, public status: number) { super(message); } }
export async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  if (!appConfig.apiUrl) throw new Error('Saved on this device. Processing is temporarily unavailable.');
  const identity = await getIdentity();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(appConfig.apiUrl + path, {
      method, signal: controller.signal,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${identity.token}` },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    let result: any;
    try { result = await response.json(); } catch { throw new ApiError('The service returned an unreadable response. Your local saves are safe.', response.status); }
    if (!response.ok) throw new ApiError(typeof result.detail === 'string' ? result.detail : 'This change could not sync. Please retry.', response.status);
    return result as T;
  } finally { clearTimeout(timer); }
}
export async function connectDevice() { return request('/devices', 'POST', await getIdentity()); }
