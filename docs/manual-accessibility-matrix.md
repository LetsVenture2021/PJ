# Manual accessibility matrix

External assistive technologies are release checks, not CI dependencies. Test at 200% and 400% zoom,
with keyboard only, online/offline transitions, streaming, upload failure, and approval accept/reject.

| Platform | Screen reader / browser    | Required checks                                                                                               |
| -------- | -------------------------- | ------------------------------------------------------------------------------------------------------------- |
| macOS    | VoiceOver / Safari         | landmarks and skip link; rotor order; streaming announcements; upload and completion status; approval buttons |
| Windows  | NVDA / Firefox or Chromium | logical tab order; visible focus; status wording; keyboard-only approval; artifact alternatives               |
| iOS      | VoiceOver / Safari         | navigation panel toggle; 44px targets; virtual-keyboard composer; orientation and safe areas                  |
| Android  | TalkBack / Chromium        | single timeline; panel toggle; virtual-keyboard composer; upload progress and offline recovery                |

Pass requires that every state has text (not color alone), focus remains visible, voice controls have
text alternatives, and reduced-motion mode has no nonessential transitions.
