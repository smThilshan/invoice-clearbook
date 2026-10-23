/** Light / dark (task 4.18): light unless the user picks dark or "match this computer". The
 * choice is remembered per computer; storage may be blocked, then light is used. */
export type Theme = 'light' | 'dark' | 'system'

const KEY = 'clearbook.theme'
const darkScheme = () => window.matchMedia('(prefers-color-scheme: dark)')

export function savedTheme(): Theme {
  try {
    const value = localStorage.getItem(KEY)
    return value === 'dark' || value === 'system' ? value : 'light'
  } catch {
    return 'light'
  }
}

export function applyTheme(theme: Theme): void {
  const dark = theme === 'dark' || (theme === 'system' && darkScheme().matches)
  document.documentElement.classList.toggle('dark', dark)
}

export function chooseTheme(theme: Theme): void {
  try {
    localStorage.setItem(KEY, theme)
  } catch {
    // not remembered this time
  }
  applyTheme(theme)
}

/** Apply the saved theme now, and follow the computer when "system" is chosen. */
export function startTheme(): void {
  applyTheme(savedTheme())
  darkScheme().addEventListener('change', () => applyTheme(savedTheme()))
}
