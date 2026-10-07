const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8080";

export const linkedInConnectUrl = `${API_URL}/api/auth/linkedin`;

export type PostStatus = "scheduled" | "processing" | "published" | "failed";

export interface Dashboard {
  linkedin_connected: boolean;
  scheduled_count: number;
  published_count: number;
  failed_count: number;
}

export interface ScheduledPost {
  id: string;
  content: string;
  scheduled_at: string;
  status: PostStatus;
  attempt_count: number;
  last_error: string | null;
  created_at: string;
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export function getDashboard(): Promise<Dashboard> {
  return request<Dashboard>("/api/dashboard");
}

export function getLinkedInStatus(): Promise<{ connected: boolean }> {
  return request<{ connected: boolean }>("/api/linkedin/status");
}

export function listPosts(): Promise<ScheduledPost[]> {
  return request<ScheduledPost[]>("/api/posts");
}

export function schedulePost(content: string, scheduledAt: string): Promise<ScheduledPost> {
  return request<ScheduledPost>("/api/posts/schedule", {
    method: "POST",
    body: JSON.stringify({ content, scheduled_at: scheduledAt }),
  });
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
    },
  });
  if (!response.ok) {
    throw new ApiError(await readError(response), response.status);
  }
  return (await response.json()) as T;
}

async function readError(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object") {
      if ("message" in body && typeof body.message === "string") {
        return body.message;
      }
      if ("detail" in body && typeof body.detail === "string") {
        return body.detail;
      }
    }
  } catch {
    return response.statusText || "Request failed";
  }
  return response.statusText || "Request failed";
}
