import { describe, expect, it } from 'vitest'

import { chooseFiles, formatBytes, MAX_UPLOAD_FILES, tooMuch } from './chosenFiles'

/** A file as the browser gives it for a chosen folder: its path inside the folder included. */
function file(path: string, size = 1000): File {
  const made = new File([new Uint8Array(size)], path.split('/').at(-1) ?? path)
  Object.defineProperty(made, 'webkitRelativePath', { value: path.includes('/') ? path : '' })
  return made
}

describe('chosen files', () => {
  it('keeps the invoices of a chosen folder, sub-folders included, and names the folder', () => {
    const choice = chooseFiles([
      file('October 2026/21/bill.pdf', 2000),
      file('October 2026/photo.JPG', 3000),
      file('October 2026/totals.xlsx'),
      file('October 2026/.DS_Store'),
      file('October 2026/~$notes.docx'),
    ])

    expect(choice.folder).toBe('October 2026')
    expect(choice.invoices.map((chosen) => chosen.path)).toEqual(['October 2026/21/bill.pdf', 'October 2026/photo.JPG'])
    expect(choice.skipped).toEqual(['October 2026/totals.xlsx'])
    expect(choice.bytes).toBe(5000)
  })

  it('has no folder for files chosen one by one', () => {
    const choice = chooseFiles([file('a.pdf'), file('b.png')])

    expect(choice.folder).toBe('')
    expect(choice.invoices.map((chosen) => chosen.path)).toEqual(['a.pdf', 'b.png'])
  })

  it('says when there is too much for one upload', () => {
    const many = chooseFiles(Array.from({ length: MAX_UPLOAD_FILES + 1 }, (_, n) => file(`F/${n}.pdf`, 1)))

    expect(tooMuch(many)).toMatch(/At most 1000 files/)
    expect(tooMuch(chooseFiles([file('F/a.pdf')]))).toBeNull()
    expect(tooMuch({ folder: '', invoices: [], skipped: [], bytes: 2 * 1024 ** 3 })).toMatch(/At most 1.0 GB/)
  })

  it('writes sizes plainly', () => {
    expect([formatBytes(300), formatBytes(65_000_000), formatBytes(2 * 1024 ** 3)]).toEqual(['1 KB', '62.0 MB', '2.0 GB'])
  })
})
