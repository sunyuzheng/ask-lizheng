import {IN_APP} from './in-app.js';

// Access belongs to verified source metadata, never a title or an Ask account tier.
export const YOUTUBE_MEMBERSHIP_URL = 'https://www.youtube.com/channel/UC_5lJHgnMP_lb_VpIiXV0hQ/join';
export const isMemberVideo = source => source?.source_visibility === 'members-only' && source.source_type?.includes('video');
export const sourceTypeLabel = source => isMemberVideo(source) ? '会员视频'
  : source?.source_type?.includes('video') ? '视频' : source?.source_type === 'context' ? 'AI整理' : '文章';
export function sourceAccessNote(source) {
  if (!isMemberVideo(source)) return '';
  const original = source.membership_platform === 'youtube' ? '原视频需 YouTube 频道会员。' : '原视频需会员观看。';
  return source.text_access === 'public' ? `文字稿已公开；${original}` : original;
}
export function transcriptQualityNote(source) {
  return source?.transcript_quality === 'uncorrected-asr' ? '自动识别稿，尚未校对。'
    : source?.transcript_quality === 'source-unverified' ? '文字稿来源尚未核实。' : '';
}
export function memberVideoUrl(source) {
  if (!isMemberVideo(source) || source.membership_platform !== 'youtube') return null;
  try {
    const url = new URL(source.url);
    if (url.protocol !== 'https:' || url.username || url.password || !['www.youtube.com', 'youtube.com', 'youtu.be'].includes(url.hostname)) return null;
    url.searchParams.delete('t'); url.searchParams.delete('start'); url.hash = '';
    return url.href;
  } catch { return null; }
}
// No join link inside the iPhone app; each source still says its video needs a channel membership.
export function memberJoinUrl(sources) {
  return !IN_APP && sources?.some(source => isMemberVideo(source) && source.membership_platform === 'youtube'
    && source.membership_url === YOUTUBE_MEMBERSHIP_URL) ? YOUTUBE_MEMBERSHIP_URL : null;
}
export function sourceCopyText(source) {
  return [`${source.title}（${source.date?.slice(0, 10) || ''}）${isMemberVideo(source) ? ' · 会员视频' : ''}`,
    sourceAccessNote(source), source.url,
    ...(isMemberVideo(source) && source.text_access === 'public' && source.public_copy_url ? [`公开文字稿：${source.public_copy_url}`] : [])]
    .filter(Boolean).join('\n');
}
