import { expect, test } from '@playwright/test'
import { distinctRefs, fileRefs, refName } from '../src/fileRefs'

// F-50: the grammar of a file reference (ui/src/fileRefs.ts). No page and no sandbox: it imports `test` from Playwright itself.

test('a reference is a token ending in html, png, jpg, jpeg or svg, in plain text, a code span or a link target', () => {
  expect(fileRefs('see mock.html now')).toEqual(['mock.html'])
  expect(fileRefs('see `shot.png`')).toEqual(['shot.png'])
  expect(fileRefs('[the mock](flow.svg)')).toEqual(['flow.svg'])
  expect(fileRefs('![shot](ui/test-results/shots/a.JPG)')).toEqual(['ui/test-results/shots/a.JPG']) // any case
  expect(fileRefs('and a.jpeg, then b.png')).toEqual(['a.jpeg', 'b.png'])
})

test('all four forms: a docs path, a bare name, an absolute or ~ path, a relative path', () => {
  const text = [
    '.aiteams/features/F-29/mock-login.html',
    'mock-login.html',
    '/Users/aditya/AITeams-worktrees/AITeams/f45/ui/test-results/shots/a.png',
    '~/AITeams-worktrees/AITeams/f45/ui/test-results/shots/b.png',
    'ui/test-results/shots/c.png',
    'qa/shots/x.png',
    './d.svg',
  ].join(' and ')
  expect(fileRefs(text)).toEqual([
    '.aiteams/features/F-29/mock-login.html',
    'mock-login.html',
    '/Users/aditya/AITeams-worktrees/AITeams/f45/ui/test-results/shots/a.png',
    '~/AITeams-worktrees/AITeams/f45/ui/test-results/shots/b.png',
    'ui/test-results/shots/c.png',
    'qa/shots/x.png',
    './d.svg',
  ])
})

test('the end of a sentence counts, a longer extension and other types do not', () => {
  expect(fileRefs('it is shot.png.')).toEqual(['shot.png'])
  expect(fileRefs('is it shot.png? yes: shot.png!')).toEqual(['shot.png'])
  expect(fileRefs('not x.html.bak or notes.md or run.py or a.pngx or b.htm')).toEqual([])
  expect(fileRefs('the .png files and .svg ones')).toEqual([]) // an extension on its own is a word
})

test('a URL is not a file reference', () => {
  expect(fileRefs('![x](https://example.com/a.png) and http://x.test/b.html')).toEqual([])
  expect(fileRefs('https://example.com/a.png but also c.png')).toEqual(['c.png'])
})

test('a repeated reference counts once, in the order first seen', () => {
  expect(fileRefs('b.png a.png b.png a.png')).toEqual(['b.png', 'a.png'])
  expect(fileRefs('x.png and ./x.png')).toEqual(['x.png', './x.png']) // different spellings are different references
})

test('every reference is returned: the cap is the surface\'s, not the grammar\'s', () => {
  expect(fileRefs(Array.from({ length: 6 }, (_, i) => `m${i}.png`).join(' '))).toHaveLength(6)
})

test('refName is the file name', () => {
  expect(refName('ui/test-results/shots/a.png')).toBe('a.png')
  expect(refName('mock-login.html')).toBe('mock-login.html')
  expect(refName('~/x/y.svg')).toBe('y.svg')
})

test('a docs file named as a docs path and as a bare name is one file, in its own feature only', () => {
  const docs = '.aiteams/features/F-3/mock.html'
  expect(distinctRefs([docs, 'mock.html'], 3)).toEqual([docs]) // the first spelling stays
  expect(distinctRefs(['mock.html', docs], 3)).toEqual(['mock.html'])
  expect(distinctRefs([docs, 'mock.html'], 4)).toEqual([docs, 'mock.html']) // another feature's file is another file
  expect(distinctRefs([docs, 'mock.html'], null)).toEqual([docs, 'mock.html'])
  expect(distinctRefs(['a/x.png', 'b/x.png'], 3)).toEqual(['a/x.png', 'b/x.png']) // the same name in two folders is two files
})
