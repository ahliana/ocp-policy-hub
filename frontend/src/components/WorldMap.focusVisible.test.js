import fs from 'fs';
import path from 'path';

// Real-world defect (WCAG 2.4.7): the ~200 country/admin-1 <path> elements
// are role="button" tabindex="0" with outline:none and no focus style
// strong enough to be perceived, so a keyboard user tabbing across the map
// can't see where focus is. jsdom doesn't apply real CSS layout/paint, so
// this reads the actual shipped stylesheet for the mechanism the fix
// depends on; the reviewer still needs to confirm it visually with a real
// keyboard-tab pass.

const CSS_PATH = path.join(__dirname, 'WorldMap.css');
const SVG_PATH = path.join(__dirname, 'WorldMapSvg.js');
const COUNTRY_VIEW_PATH = path.join(__dirname, 'CountryView.js');

function readFile(filePath) {
  return fs.readFileSync(filePath, 'utf8');
}

describe('WorldMap keyboard focus visibility fix', () => {
  it('renders both country paths (WorldMapSvg) and admin-1 paths (CountryView) as role="button" - the selector target the fix relies on', () => {
    expect(readFile(SVG_PATH)).toMatch(/role=\{tracked \? 'button' : undefined\}/);
    expect(readFile(COUNTRY_VIEW_PATH)).toMatch(/role=\{tracked \? 'button' : undefined\}/);
  });

  it('defines a dedicated focus-ring color distinct from every choropleth fill, in both light and dark mode', () => {
    const css = readFile(CSS_PATH);

    const rootBlock = css.match(/\.world-map\s*{([\s\S]*?)}/)[1];
    const darkBlock = css.match(/@media \(prefers-color-scheme: dark\)\s*{\s*\.world-map\s*{([\s\S]*?)}/)[1];

    const lightFocusRing = rootBlock.match(/--wm-focus-ring:\s*([^;]+);/)[1].trim();
    const darkFocusRing = darkBlock.match(/--wm-focus-ring:\s*([^;]+);/)[1].trim();

    const fillVarNames = ['--wm-map-untracked', '--wm-map-tracked0', '--wm-map-bin1', '--wm-map-bin2', '--wm-map-bin3'];
    const lightFillValues = fillVarNames.map((name) => rootBlock.match(new RegExp(`${name}:\\s*([^;]+);`))[1].trim());
    const darkFillValues = fillVarNames.map((name) => darkBlock.match(new RegExp(`${name}:\\s*([^;]+);`))[1].trim());

    expect(lightFocusRing).toBeTruthy();
    expect(darkFocusRing).toBeTruthy();
    expect(lightFillValues).not.toContain(lightFocusRing);
    expect(darkFillValues).not.toContain(darkFocusRing);
  });

  it('gives svg path[role="button"]:focus-visible a stroke clearly wider than the plain hover stroke', () => {
    const css = readFile(CSS_PATH);
    const focusRuleMatch = css.match(/svg path\[role="button"\]:focus-visible\s*{([^}]*)}/);

    expect(focusRuleMatch).not.toBeNull();
    const focusRule = focusRuleMatch[1];

    expect(focusRule).toMatch(/stroke:\s*var\(--wm-focus-ring\)/);
    const strokeWidthMatch = focusRule.match(/stroke-width:\s*([\d.]+)/);
    expect(strokeWidthMatch).not.toBeNull();
    // The existing hover/hit stroke-width is 1.1 - the focus ring must be
    // meaningfully thicker so it reads as a distinct, deliberate indicator.
    expect(Number(strokeWidthMatch[1])).toBeGreaterThan(1.1);
  });

  it('keeps the focus ring fully opaque even on a dimmed (legend-filtered) country', () => {
    const css = readFile(CSS_PATH);
    const focusRuleMatch = css.match(/svg path\[role="button"\]:focus-visible\s*{([^}]*)}/);
    expect(focusRuleMatch[1]).toMatch(/opacity:\s*1\b/);
  });
});
