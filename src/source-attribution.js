// Source-owned labels; model prose does not assign a speaker or data layer.
export function sourceAttributionLabel(source) {
  if (source?.data_layer === 'derived') {
    const origin = String(source.content_origin || '');
    const method = String(source.generation_method || '');
    return source.author === 'AI' || origin.startsWith('ai-') || method.includes('ai-written') ? 'AI整理' : '整理稿';
  }
  if (source?.speaker_id === 'unknown') return '说话人待核';
  if (source?.speaker_id && source?.speaker_name) return `发言：${source.speaker_name}`;
  if (source?.speaker_classification === 'mixed-or-unresolved') return '多人发言 · 说话人待核';
  return '';
}
