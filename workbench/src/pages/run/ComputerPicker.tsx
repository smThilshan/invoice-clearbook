import { FileText, FolderOpen, Files } from 'lucide-react'
import { type ChangeEvent, useEffect, useRef } from 'react'

import { Button } from '@/components/ui/button'
import { type Choice, chooseFiles, formatBytes, tooMuch } from '@/lib/chosenFiles'

/** Choose a folder (sub-folders included) or files on this computer, to upload and run
 * (decision 78). The browser's own window opens; nothing is sent until the run starts. */
export function ComputerPicker({ choice, onChoose }: { choice: Choice | undefined; onChoose: (choice: Choice) => void }) {
  const folderInput = useRef<HTMLInputElement>(null)
  const filesInput = useRef<HTMLInputElement>(null)

  // Not in React's types: lets the input pick a whole folder, in every current browser.
  useEffect(() => folderInput.current?.setAttribute('webkitdirectory', ''), [])

  function picked(event: ChangeEvent<HTMLInputElement>) {
    const files = event.target.files
    if (files && files.length > 0) onChoose(chooseFiles(Array.from(files)))
    event.target.value = '' // the same folder can be chosen again
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" onClick={() => folderInput.current?.click()}>
          <FolderOpen aria-hidden />
          Choose folder
        </Button>
        <Button type="button" variant="ghost" onClick={() => filesInput.current?.click()}>
          <Files aria-hidden />
          Choose files
        </Button>
        <input ref={folderInput} type="file" multiple hidden aria-label="Folder to upload" onChange={picked} />
        <input
          ref={filesInput}
          type="file"
          multiple
          hidden
          aria-label="Files to upload"
          accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
          onChange={picked}
        />
      </div>
      {choice && <ChoiceSummary choice={choice} />}
    </div>
  )
}

function ChoiceSummary({ choice }: { choice: Choice }) {
  const count = choice.invoices.length
  const files = `${count} invoice file${count === 1 ? '' : 's'} (${formatBytes(choice.bytes)})`
  const problem = tooMuch(choice)
  return (
    <div className="space-y-1 text-sm" data-testid="chosen">
      <p className="flex items-center gap-1.5">
        <FileText className="size-4 text-muted-foreground" aria-hidden />
        {count === 0
          ? 'No invoice files (PDF or photo) in what you chose.'
          : choice.folder
            ? `${files} in “${choice.folder}”, sub-folders included.`
            : `${files} chosen.`}
      </p>
      {choice.skipped.length > 0 && (
        <p className="text-muted-foreground">
          {choice.skipped.length} other file{choice.skipped.length === 1 ? ' is' : 's are'} left out:{' '}
          {choice.skipped.slice(0, 3).map((path) => path.split('/').at(-1)).join(', ')}
          {choice.skipped.length > 3 ? ', …' : ''}
        </p>
      )}
      {problem && <p className="text-destructive">{problem}</p>}
    </div>
  )
}
