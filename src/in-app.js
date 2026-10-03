// The iPhone app shows this page with AskLizhengApp/<version> in its user agent. App Store rules
// keep purchase links and prices out of the app; verifying an existing Founding Member stays.
export const IN_APP = typeof navigator !== 'undefined' && /AskLizhengApp\//.test(navigator.userAgent);
