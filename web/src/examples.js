/* Worked examples for visitors without an API key.
 *
 * The UI states this as well: the "after" texts are written by hand to
 * illustrate what each rewrite strength aims for. They are not model output,
 * and the page does not claim they are. What is real:
 *
 *   - the divergence score, computed live by the same function that scores a
 *     genuine rewrite (see rewrite.js)
 *   - the prompt shown alongside, byte-identical to what would be sent
 *   - the Layer A pass over the text, which is the real implementation
 *
 * A visitor who adds a key gets the real thing on their own text.
 */

export const EXAMPLES = [
  {
    id: 'report',
    strength: 'paraphrase',
    title: 'Corporate summary',
    before:
      'The committee released its findings on 14 March 2023. Revenue grew 12% ' +
      'to $4.2 million, driven primarily by strong performance in the ' +
      'enterprise segment. Moreover, customer retention improved significantly ' +
      'over the same period. It is important to note that these results exceed ' +
      'the guidance issued in January.',
    after:
      'On 14 March 2023 the committee published what it had found. Revenue ' +
      'climbed 12%, reaching $4.2 million, and most of that came from the ' +
      'enterprise segment. Retention also rose over the same stretch. Both ' +
      'figures land above the guidance the committee gave in January.',
  },
  {
    id: 'humanize',
    strength: 'humanize',
    title: 'Blog paragraph',
    before:
      'In the ever-evolving landscape of modern software development, it is ' +
      'crucial to delve into the intricate tapestry of testing methodologies. ' +
      'Furthermore, developers must navigate a myriad of frameworks. In ' +
      'conclusion, choosing the right approach is paramount to success.',
    after:
      'Testing is where most software projects quietly succeed or fail. There ' +
      'are more frameworks than anyone can reasonably evaluate, and picking ' +
      'one is less about finding the best and more about finding the one your ' +
      'team will actually keep using. That choice matters more than the tool.',
  },
  {
    id: 'technical',
    strength: 'paraphrase',
    title: 'Technical writing, facts preserved',
    before:
      'The parser reads a 4-byte big-endian length, then a 4-byte chunk type, ' +
      'then that many bytes of payload, followed by a CRC32 of the type and ' +
      'payload concatenated. Chunks whose type has bit 5 of the first byte set ' +
      'are ancillary and may be discarded safely.',
    after:
      'Each chunk starts with its length as a 4-byte big-endian integer, ' +
      'followed by a 4-byte type code. The payload of that stated length comes ' +
      'next, and a CRC32 covering the type and payload together closes it out. ' +
      'When bit 5 of the type’s first byte is set, the chunk is ancillary and ' +
      'discarding it is safe.',
  },
  {
    id: 'code',
    strength: 'code',
    title: 'Code: comments renamed, behaviour identical',
    before: `def process(items):
    # Loop through all the items and total them up
    total = 0
    for thing in items:
        # Skip anything that is None
        if thing is None:
            continue
        total += thing
    return total`,
    after: `def process(items):
    # Accumulate the values, ignoring gaps in the input
    running = 0
    for value in items:
        # A missing entry contributes nothing
        if value is None:
            continue
        running += value
    return running`,
    note:
      'The public name process() and the behaviour are untouched. Only the ' +
      'comments and the local variable names changed.',
  },
  {
    id: 'backtranslate',
    strength: 'backtranslate',
    title: 'Backtranslation through French',
    before:
      'The library was quiet except for the sound of pages turning. She had ' +
      'been reading for three hours and had not noticed the light fading.',
    after:
      'Apart from the rustle of turning pages, the library was silent. She had ' +
      'spent three hours with her book without noticing that the light had gone.',
    note: 'Two model calls: English to French, then French back to English.',
  },
  {
    id: 'structural',
    strength: 'structural',
    title: 'Structural rewrite from an outline',
    before:
      'Our new pricing takes effect on 1 June. The basic tier stays at $10 per ' +
      'month. The pro tier rises from $25 to $30. Existing pro customers keep ' +
      'the old rate until January. Annual billing saves two months.',
    after:
      'Pricing changes on 1 June. Basic is unchanged at $10 a month, while pro ' +
      'moves from $25 to $30. If you are already on pro, you stay at the old ' +
      'rate through to January. Paying annually still works out two months cheaper.',
    note:
      'The model first extracts every claim as a bullet list, then writes a ' +
      'fresh document from that outline. All five facts survive.',
  },
];

/* Text carrying real invisible watermarks, for the Layer A demo. Built at
 * runtime so the characters are unambiguously present. */
export function watermarkedSample() {
  const tag = (s) =>
    [...s].map((ch) => String.fromCodePoint(0xe0000 + ch.codePointAt(0))).join('');
  return (
    'Quarterly​ Report' + tag('ID:4471') + '\n\n' +
    'Revenue rose 12% this⁠ quarter.­ The board approved​ the ' +
    'budget﻿ on Tuesday.\n\n' +
    'Prepared by the finance team \u{1F469}‍\u{1F4BC}. Contact us with questions.'
  );
}

export const SAMPLE_NOTE =
  'This sample contains a zero-width space, a word joiner, a soft hyphen, ' +
  'a BOM, non-breaking spaces, and a hidden ID smuggled in Unicode tag ' +
  'characters. The emoji joiner is legitimate and is deliberately kept.';
