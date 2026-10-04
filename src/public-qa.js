// What we tell people about public Q&A, in one place. The user's picture for it (2026-10-04): asking
// here is like raising your hand at a recorded talk, so the questions and answers are public but
// carry no names, and the situation is a note passed to the stage. lizheng.ai uses the same words
// on its homepage section and in its privacy policy: its scripts/sync-ask-app.mjs copies this file
// to shared/ask-public-qa.js, and its tests check the policy still says the same. Edit it here.
//
// In the Chinese text {owner} is who answers from his own material (立正 on this page, 我 on the
// homepage), {self} is how the same sentence refers back to him (他 or 我), and {answerer} is the
// service that writes the answers (from /api/meta). The English text is the homepage's, in his voice.
export const PUBLIC_QA = {
  zh: {
    notice: '这里像在讲座上举手提问：问答会公开，但不记名。只写愿意当众说的话。',
    title: '这里是公开问答',
    about: [
      '像在一场有录像的讲座上举手提问：在场的人都听得到，录像会放到网上，也搜得到。',
      '问题、回答和出处都会保存。常见的问题去掉个人信息后，放进「别人在问什么」；你也可以把自己的问答分享出去。{owner}会从这些问题里找选题，也用它们改进回答。',
      '不记名：提问不和邮箱、账号或IP记在一起，我们不知道是谁问的；登录只用来核验Founding身份。但你写的内容本身可能认出你，发给AI前也不会自动删掉，所以别写名字、公司和联系方式。',
      {kept: '「结合我的处境」像递给台上的一张纸条：只给{owner}分析，不单独公开；但回答可能提到纸条上的内容。',
        notKept: '「结合我的处境」像递给台上的一张纸条：只用来回答你，我们不保存；但回答可能提到纸条上的内容。'},
      '回答由AI根据{owner}公开的文章和视频整理，不是{self}本人回复。提问交给AI处理：{answerer}写回答，OpenAI找资料。',
    ],
    situation: {
      kept: '这里填的内容只给{owner}分析，不单独公开；回答可能提到其中的细节。',
      notKept: '这里填的内容只用来生成这次回答，我们不保存；回答可能提到其中的细节。',
    },
    share: '分享页只显示这个问题和回答，不显示你填的处境和其他提问。',
    policy: '简单说：这里像在一场有录像的讲座上举手提问。问题和回答会公开，别人看得到、搜得到；但不记名，我们不知道是谁问的。请只写愿意当众说的话。',
    answerer: 'AI模型',
  },
  en: {
    notice: 'Like raising your hand at a talk: questions and answers are public, with no names attached. Write only what you’d say in front of everyone.',
    title: 'This is public Q&A',
    about: [
      'Think of raising your hand at a talk that’s being recorded: everyone in the room hears you, and the recording goes online, where search engines can find it.',
      'Questions, answers and sources are all kept. Common questions appear in “What others are asking” with personal details removed, and you can share your own. I look through the questions for topics to write about, and use them to improve the answers.',
      'No names attached: questions aren’t linked to an email, account or IP address, so we don’t know who asked. Signing in only checks Founding Member status. But what you write may still identify you, and it isn’t redacted before AI processing, so leave out names, companies and contact details.',
      {kept: '“Apply to my situation” is like passing a note to the stage: only I see it, for analysis, and it isn’t published on its own, but the answer may mention what it said.',
        notKept: '“Apply to my situation” is like passing a note to the stage: it’s only used to answer you and we don’t keep it, but the answer may mention what it said.'},
      'Answers are written by AI from my public articles and videos, not by me. Your question goes to AI services: {answerer} writes the answer and OpenAI finds the sources.',
    ],
    situation: {
      kept: 'Only I see what you write here, for analysis, and it isn’t published on its own, though the answer may mention its details.',
      notKept: 'What you write here is only used for this answer. We don’t keep it, though the answer may mention its details.',
    },
    share: 'The shared page shows only this question and answer, not your situation or other questions.',
    policy: 'In short: asking here is like raising your hand at a recorded talk. Questions and answers are public, others can see them and search engines can find them, but no names are attached and we don’t know who asked. Write only what you’d say in front of everyone.',
    answerer: 'an AI model',
  },
};

// Who writes the answers for a model the service names (/api/meta model), as confirmed with Builder
// Space (2026-10-04). An unknown model gets no name rather than a guess.
export const answerService = model => {
  if (model === 'deepseek-v4-flash' || model === 'deepseek-v4-pro') return 'DeepSeek';
  if (model === 'gpt-5') return 'OpenAI';
  if (model === 'grok-4.5') return 'xAI';
  if (/^gemini(?:-|$)/.test(model || '')) return 'Google';
  if (/^kimi(?:-|$)/.test(model || '')) return 'Moonshot';
  return null;
};

// The text for one language and voice, with the situation sentences chosen by whether the service
// keeps the situation (/api/meta ops_logging.context_archive).
export function publicQaCopy(lang, {owner, self, answerer, contextKept}) {
  const copy = PUBLIC_QA[lang];
  const fill = text => text.replaceAll('{owner}', owner).replaceAll('{self}', self).replaceAll('{answerer}', answerer || copy.answerer);
  const pick = text => fill(typeof text === 'string' ? text : contextKept ? text.kept : text.notKept);
  return {
    notice: copy.notice,
    title: copy.title,
    about: copy.about.map(pick),
    situation: pick(copy.situation),
    share: copy.share,
    policy: copy.policy,
  };
}
