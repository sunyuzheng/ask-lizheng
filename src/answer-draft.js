// The answer while it is being written: what a turn keeps of the answer service's `progress` and
// `partial` events until `result` replaces it (docs/API.md). The model writes the summary first, so
// the first partial is usually the summary alone; each later one adds a section. Every partial is
// the whole draft so far, already checked by the service, with the sources it cites under the
// candidates' numbers.

// A partial replaces the draft before it; its sources join the candidates already shown, by number.
export function afterPartial(turn, partial) {
  return {partial, previewSources: [...new Map([...(turn.previewSources || []), ...(partial.sources || [])].map(source => [source.id, source])).values()]};
}

// `repairing`: the service found the answer it was writing invalid and is writing it again, so
// what was shown of it (the summary too) is withdrawn until the repaired one arrives.
export function afterProgress(progress) {
  return {progress, ...(progress.stage === 'repairing' ? {partial: undefined} : {})};
}
