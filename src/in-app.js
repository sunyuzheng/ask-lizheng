// The iPhone app shows this page with AskLizhengApp/<version> in its user agent. App Store rules
// keep purchase links and prices out of the app; verifying an existing Founding Member stays.
// A WeChat mini program shows it in a web-view, whose user agent says miniProgram; the same rules apply.
export const IN_APP = typeof navigator !== 'undefined' && /AskLizhengApp\/|miniProgram/i.test(navigator.userAgent);
