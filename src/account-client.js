async function readAskAccount() {
  try {
    const response = await fetch("/api/ask-lizheng/auth/session", { credentials: "same-origin", cache: "no-store" });
    if (!response.headers.get("content-type")?.includes("application/json")) return null;
    const value = await response.json();
    if (!response.ok) return { enabled: true, unavailable: true };
    if (typeof value.enabled !== "boolean") return null;
    return value;
  } catch {
    return null;
  }
}
const DRAFT = "ask-login-draft-v1";
function takeAskDraft() {
  try {
    const raw = sessionStorage.getItem(DRAFT);
    sessionStorage.removeItem(DRAFT);
    if (!raw) return null;
    const value = JSON.parse(raw);
    if (typeof value.question !== "string" || typeof value.context !== "string" || !["understand", "apply", "find"].includes(value.intent) || !Number.isFinite(value.saved) || Date.now() - value.saved > 6e5) return null;
    return { question: value.question.slice(0, 2e3), context: value.context.slice(0, 2500), intent: value.intent };
  } catch {
    return null;
  }
}
function finishAskLogin() {
  const url = new URL(location.href);
  if (url.searchParams.get("ask_login") !== "done") return;
  url.searchParams.delete("ask_login");
  history.replaceState(null, "", url);
  if (window.opener) window.opener.postMessage({ type: "ask-login-complete" }, location.origin);
  window.close();
}
function beginAskLogin(draft, done) {
  const base = `/api/ask-lizheng/auth/login?return=${encodeURIComponent(location.pathname)}`;
  const popup = window.open(`${base}&popup=1`, "ask-academy-login", "width=520,height=720");
  if (!popup) {
    try {
      sessionStorage.setItem(DRAFT, JSON.stringify({ ...draft, saved: Date.now() }));
    } catch {
    }
    location.assign(base);
    return () => {
    };
  }
  let finished = false;
  const cleanup = () => {
    clearInterval(timer);
    clearTimeout(expiry);
    window.removeEventListener("message", message);
  };
  const complete = () => {
    if (finished) return;
    finished = true;
    cleanup();
    done();
  };
  const message = (event) => {
    if (event.origin === location.origin && event.source === popup && event.data?.type === "ask-login-complete") complete();
  };
  window.addEventListener("message", message);
  const timer = setInterval(() => {
    if (popup.closed) complete();
  }, 500);
  const expiry = setTimeout(cleanup, 66e4);
  return cleanup;
}
async function logoutAsk() {
  await fetch("/api/ask-lizheng/auth/logout", { method: "POST", credentials: "same-origin", cache: "no-store" });
}
export {
  beginAskLogin,
  finishAskLogin,
  logoutAsk,
  readAskAccount,
  takeAskDraft
};
