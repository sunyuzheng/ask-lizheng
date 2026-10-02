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
// Runs where popup sign-in lands. Reports back to the page that opened the
// popup and closes it. Returns true when sign-in just finished, so a page that
// stays open (a browser that opened the popup as this tab) shows the result.
function finishAskLogin() {
  const url = new URL(location.href);
  if (url.searchParams.get("ask_login") !== "done") return false;
  url.searchParams.delete("ask_login");
  history.replaceState(null, "", url);
  if (window.opener && window.opener !== window) {
    window.opener.postMessage({ type: "ask-login-complete" }, location.origin);
    window.close();
  }
  return true;
}
const loginPath = () => `/api/ask-lizheng/auth/login?return=${encodeURIComponent(location.pathname)}`;
// Signs in in this tab. The unsent question waits in sessionStorage, and
// finding it on return (takeAskDraft) is how the page knows to show the result.
function askLoginHere(draft) {
  try {
    sessionStorage.setItem(DRAFT, JSON.stringify({ ...draft, saved: Date.now() }));
  } catch {
  }
  location.assign(loginPath());
}
// Signs in in a popup, or in this tab when the popup is blocked. `done` runs
// once, when the popup reports back or closes. The returned cleanup stops
// waiting and, given true, also closes the popup.
function beginAskLogin(draft, done) {
  const popup = window.open(`${loginPath()}&popup=1`, "ask-academy-login", "width=520,height=720");
  if (!popup) {
    askLoginHere(draft);
    return () => {
    };
  }
  let finished = false;
  const cleanup = (closePopup = false) => {
    clearInterval(timer);
    clearTimeout(expiry);
    window.removeEventListener("message", message);
    if (closePopup) popup.close();
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
  // A sign-in transaction lives 10 minutes; after that the popup cannot finish.
  const expiry = setTimeout(complete, 66e4);
  return cleanup;
}
async function logoutAsk() {
  await fetch("/api/ask-lizheng/auth/logout", { method: "POST", credentials: "same-origin", cache: "no-store" });
}
export {
  askLoginHere,
  beginAskLogin,
  finishAskLogin,
  logoutAsk,
  readAskAccount,
  takeAskDraft
};
