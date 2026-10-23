import '@testing-library/jest-dom/vitest'

import { cleanup, configure } from '@testing-library/react'
import { afterEach } from 'vitest'

// Pages load their code when first opened (task 4.15); in tests the first load of a page can
// take longer than Testing Library's default one second.
configure({ asyncUtilTimeout: 3000 })

// Testing Library cleans up automatically only with Vitest globals, which we keep off.
afterEach(() => {
  cleanup()
})

// Radix (Select and other popovers) calls these browser APIs, which jsdom does not have.
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false
  Element.prototype.releasePointerCapture = () => {}
  Element.prototype.setPointerCapture = () => {}
}
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}
if (!('ResizeObserver' in globalThis)) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}
