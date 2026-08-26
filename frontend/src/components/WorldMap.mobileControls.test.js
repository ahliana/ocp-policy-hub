import fs from 'fs';
import path from 'path';
import React from 'react';
import { render, screen } from '@testing-library/react';
import WorldMap from './WorldMap';

// Real-world defect: at a 375px mobile viewport the map SVG shrinks and the
// +/-/Reset control cluster (.wm-controls), an absolutely-positioned overlay
// in the top-left of .wm-stage, sits on top of the map and intercepts taps
// on countries underneath it (Germany and the US were the two reported).
// jsdom does not apply real CSS cascade/layout, so the fix is verified two
// ways: (1) a DOM-order assertion tying the mobile "flow below the map"
// mechanism to something JS can check, and (2) reading the actual shipped
// stylesheet for the specific rules the fix depends on. The real 375px
// visual/tap check still needs the reviewer's browser.

const CSS_PATH = path.join(__dirname, 'WorldMap.css');

function readCss() {
  return fs.readFileSync(CSS_PATH, 'utf8');
}

const COVERAGE = {
  countries: [
    { name: 'United States', slug: 'us', iso_numeric: '840', sources: 1, policies: 1, top_policy_names: [] },
    { name: 'Germany', slug: 'germany', iso_numeric: '276', sources: 1, policies: 1, top_policy_names: [] },
  ],
  supranational: [],
  totals: { sources: 2, policies: 2 },
};

function mockFetch() {
  return jest.fn(async (url) => {
    if (String(url).includes('/api/coverage')) {
      return { ok: true, json: async () => COVERAGE };
    }
    return { ok: false, text: async () => 'not found' };
  });
}

afterEach(() => {
  jest.restoreAllMocks();
});

describe('WorldMap mobile zoom-control overlap fix', () => {
  it('renders the zoom controls after the map SVG in document order, so an in-flow mobile layout stacks them below the map, not above it', async () => {
    global.fetch = mockFetch();
    const { container } = render(<WorldMap onSelectPlace={jest.fn()} />);

    await screen.findByRole('button', { name: /United States of America/ });

    const stage = container.querySelector('.wm-stage');
    const svg = stage.querySelector('svg');
    const controls = stage.querySelector('.wm-controls');

    expect(svg).not.toBeNull();
    expect(controls).not.toBeNull();
    // eslint-disable-next-line no-bitwise
    expect(svg.compareDocumentPosition(controls) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('scopes pointer events to the buttons themselves, not the controls container, so no covering area blocks taps on the map beneath', () => {
    const css = readCss();
    const controlsRule = css.match(/\.wm-controls\s*{[^}]*}/);
    const buttonRule = css.match(/\.wm-control-btn\s*{[^}]*}/);

    expect(controlsRule).not.toBeNull();
    expect(controlsRule[0]).toMatch(/pointer-events:\s*none/);
    expect(buttonRule).not.toBeNull();
    expect(buttonRule[0]).toMatch(/pointer-events:\s*auto/);
  });

  it('takes the controls out of the absolute-overlay flow at mobile widths', () => {
    const css = readCss();
    const mobileBlockMatch = css.match(/@media \(max-width: 640px\) {([\s\S]*)}\s*$/);

    expect(mobileBlockMatch).not.toBeNull();
    const mobileControlsRule = mobileBlockMatch[1].match(/\.wm-controls\s*{[^}]*}/);

    expect(mobileControlsRule).not.toBeNull();
    expect(mobileControlsRule[0]).toMatch(/position:\s*static/);
  });
});
