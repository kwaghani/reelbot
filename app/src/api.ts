import { apiHostLabel, appConfig, getApiConfigIssue } from "./config";

export type SavedItem = {
  id: string | null;
  job_id?: string | null;
  created_at?: string | null;
  place_name: string | null;
  category: string | null;
  location_text: string | null;
  list_name: string | null;
  subfolder: string | null;
  source_url: string | null;
  status: "saved" | "processing" | "error" | "done";
  message: string | null;
  lat: number | null;
  lng: number | null;
  price_tier: string | null;
  tags: string[];
  save_count: number;
};

export type Group = {
  id: string;
  name: string;
  join_code: string | null;
  member_count: number;
  item_count: number;
};

type ApiErrorBody = {
  detail?: string;
};

class ApiRequestError extends Error {
  constructor(message: string, readonly terminalJobFailure = false, readonly httpStatus = 0) { super(message); }
}

export function isAuthorizationError(error: unknown): boolean {
  return error instanceof ApiRequestError && [401, 403].includes(error.httpStatus);
}

const apiContext = { deviceId: "", groupId: "", token: "" };

export function getDeviceCredentials() { return { deviceId: apiContext.deviceId, token: apiContext.token }; }

export function setApiContext(context: { deviceId?: string; groupId?: string; token?: string }): void {
  if (context.token !== undefined) apiContext.token = context.token;
  if (context.deviceId !== undefined) {
    apiContext.deviceId = context.deviceId;
  }
  if (context.groupId !== undefined) {
    apiContext.groupId = context.groupId;
  }
}

function scopeParams(): string {
  const params = new URLSearchParams();
  if (apiContext.groupId) {
    params.set("group_id", apiContext.groupId);
  }
  if (apiContext.deviceId) {
    params.set("device_id", apiContext.deviceId);
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

function assertApiConfig() {
  const issue = getApiConfigIssue();
  if (issue) {
    throw new Error(issue);
  }
}

function normalizeError(error: unknown): Error {
  if (error instanceof SyntaxError) {
    return new Error(`ReelBot API at ${apiHostLabel()} returned an invalid response.`);
  }

  if (error instanceof TypeError || (error instanceof Error && /Network request failed/i.test(error.message))) {
    return new Error("Cannot connect right now. Check your connection and try again.");
  }

  return error instanceof Error ? error : new Error("Could not reach ReelBot API.");
}

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  assertApiConfig();

  const controller = new AbortController();
  const forwardAbort = () => controller.abort();
  init.signal?.addEventListener("abort", forwardAbort);
  if (init.signal?.aborted) controller.abort();
  const timeout = setTimeout(() => controller.abort(), 40000);
  try {
    const response = await fetch(`${appConfig.apiUrl}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        "x-api-key": appConfig.apiKey,
        ...(apiContext.token ? { Authorization: `Bearer ${apiContext.token}` } : {}),
        ...(init.headers ?? {})
      }
    });

    const text = await response.text();
    let body: ApiErrorBody | undefined;
    try { body = text ? JSON.parse(text) as ApiErrorBody : undefined; }
    catch { throw new Error(response.ok ? "The server returned an invalid response. Try again." : `Server unavailable (${response.status}). Try again shortly.`); }

    if (!response.ok) {
      throw new ApiRequestError(body?.detail || `ReelBot API returned ${response.status}.`, response.headers?.get("X-ReelBot-Job-Terminal") === "true", response.status);
    }

    return body as T;
  } catch (error) {
    if (controller.signal.aborted) throw new Error("Request timed out or was cancelled. Check your connection and retry.");
    throw normalizeError(error);
  } finally {
    clearTimeout(timeout);
    init.signal?.removeEventListener("abort", forwardAbort);
  }
}

export function registerDevice(userName = "Friend"): Promise<{ device_id: string; token: string }> {
  return requestJson("/devices", { method: "POST", body: JSON.stringify({ user_name: userName }) });
}

export type Job = { id: string; status: string; message?: string; item_id?: string; answer?: string; sources?: QuerySource[] };
export function getJob(id: string): Promise<Job> { return requestJson(`/jobs/${encodeURIComponent(id)}`); }

export function getItems(): Promise<SavedItem[]> {
  return requestJson<SavedItem[]>(`/items${scopeParams()}`);
}

export function getGroupItems(groupId: string): Promise<SavedItem[]> {
  const params = new URLSearchParams({ group_id: groupId });
  if (apiContext.deviceId) {
    params.set("device_id", apiContext.deviceId);
  }
  return requestJson<SavedItem[]>(`/items?${params.toString()}`);
}

export async function deleteItem(id: string, groupId?: string): Promise<void> {
  const query = groupId ? `?group_id=${encodeURIComponent(groupId)}&device_id=${encodeURIComponent(apiContext.deviceId)}` : scopeParams();
  await requestJson<{ status: string }>(`/items/${id}${query}`, { method: "DELETE" });
}

export async function getGroups(userName: string): Promise<Group[]> {
  const params = new URLSearchParams({ device_id: apiContext.deviceId, user_name: userName });
  return requestJson<Group[]>(`/groups?${params.toString()}`);
}

export function createGroup(name: string, userName: string): Promise<Group> {
  return requestJson<Group>("/groups", {
    method: "POST",
    body: JSON.stringify({ name, user_name: userName, device_id: apiContext.deviceId })
  });
}

export function joinGroup(code: string, userName: string): Promise<Group> {
  return requestJson<Group>("/groups/join", {
    method: "POST",
    body: JSON.stringify({ code, user_name: userName, device_id: apiContext.deviceId })
  });
}

export function addItemsToGroup(
  targetGroupId: string,
  sourceGroupId: string,
  itemIds: string[],
  userName: string
): Promise<{ added: number; already_present: number }> {
  return requestJson<{ added: number; already_present: number }>(
    `/groups/${targetGroupId}/items`,
    {
      method: "POST",
      body: JSON.stringify({
        source_group_id: sourceGroupId,
        item_ids: itemIds,
        user_name: userName,
        device_id: apiContext.deviceId
      })
    }
  );
}


export type QuerySource = {
  title: string;
  url: string;
};

export type QueryAnswer = {
  answer: string;
  sources: QuerySource[];
};

export type ChatTurn = {
  role: "user" | "assistant";
  text: string;
};

const pendingQuestions = new Map<string, string>();

export async function askQuestion(text: string, userName: string, history: ChatTurn[] = []): Promise<QueryAnswer> {
  const key = JSON.stringify([apiContext.deviceId, apiContext.groupId, text, history]);
  const requestId = pendingQuestions.get(key) || `query-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  pendingQuestions.set(key, requestId);
  const body = await requestJson<{ answer: string; sources?: QuerySource[]; status?: string; job_id?: string }>("/query", {
    method: "POST",
    body: JSON.stringify({
      text,
      request_id: requestId,
      user_name: userName,
      history: history.slice(-12).map((turn) => ({ role: turn.role, text: turn.text.slice(0, 1400) })),
      device_id: apiContext.deviceId || undefined,
      group_id: apiContext.groupId || undefined
    })
  }).catch((error: unknown) => {
    // Unknown network outcomes retry the same job; confirmed failures may start a fresh attempt.
    if (error instanceof ApiRequestError && error.terminalJobFailure) pendingQuestions.delete(key);
    throw error;
  });

  if (body.status === "processing" && body.job_id) {
    const deadline = Date.now() + 120000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      const job = await getJob(body.job_id);
      if (job.status === "done" && job.answer) { pendingQuestions.delete(key); return { answer: job.answer, sources: job.sources ?? [] }; }
      if (["error", "cancelled"].includes(job.status)) { pendingQuestions.delete(key); throw new Error(job.message || "Could not answer that question."); }
    }
    throw new Error("Your question is still processing. Try again shortly.");
  }
  if (typeof body?.answer !== "string" || !body.answer.trim() || body.status === "processing") {
    throw new Error("The answer was not confirmed. Try again shortly.");
  }
  pendingQuestions.delete(key);
  return { answer: body.answer, sources: body.sources ?? [] };
}

export async function shareReel(url: string, userName: string, signal?: AbortSignal): Promise<{ status: string; job_id: string }> {
  const result = await requestJson<{ status: string; job_id: string }>("/share", {
    method: "POST",
    signal,
    body: JSON.stringify({
      url,
      user_name: userName,
      device_id: apiContext.deviceId,
      group_id: apiContext.groupId || undefined
    })
  });
  if (result?.status !== "queued" || !result.job_id) throw new Error("Import was not confirmed. Check Saved and retry.");
  return result;
}
