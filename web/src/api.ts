import type {
  Cart,
  CartAction,
  ChatReply,
  ChatRequest,
  CheckoutRequest,
  Voucher,
} from "./contracts";

const TIMEOUT_MS = 45_000;

// status 0: no answer (network or timeout). The message is for the user, no stack traces.
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  let r: Response;
  try {
    r = await fetch(url, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctl.signal,
    });
  } catch {
    throw new ApiError(0, "Сервер не ответил. Проверьте связь и попробуйте ещё раз.");
  } finally {
    clearTimeout(timer);
  }
  if (!r.ok) {
    // 409: the server explains in Russian why the cart or the checkout stopped.
    if (r.status === 409) {
      const detail = await r.json().then((b) => b?.detail, () => null);
      if (typeof detail === "string") throw new ApiError(409, detail);
    }
    const text =
      r.status >= 500
        ? "Сервер не смог обработать запрос. Попробуйте ещё раз."
        : `Запрос не выполнен (код ${r.status}).`;
    throw new ApiError(r.status, text);
  }
  return (await r.json()) as T;
}

export function chat(req: ChatRequest): Promise<ChatReply> {
  return call("POST", "/api/chat", req);
}

// null: the server does not know this session (expired store, other database).
export async function getSession(id: string): Promise<ChatReply | null> {
  try {
    return await call<ChatReply>("GET", `/api/session/${encodeURIComponent(id)}`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

export function cartAction(a: CartAction): Promise<Cart> {
  return call("POST", "/api/cart", a);
}

// 409: the cart total differs from confirm_total_rub.
export function checkout(req: CheckoutRequest): Promise<Voucher> {
  return call("POST", "/api/checkout", req);
}
