import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import KeywordsPanel from './KeywordsPanel';

const KEYWORDS_RESPONSE = {
  categories: {
    subject: {
      weight: 3.0,
      description: 'Core subject matter',
      terms: { en: ['waste heat', 'heat pump'], de: ['Abwärme'] },
    },
    context: {
      weight: 1.0,
      description: 'Context',
      terms: { en: ['data center'] },
    },
  },
  thresholds: { minimum_keyword_score: 5.0, minimum_matches: 2 },
  exclusions: ['job opening'],
  url_bonuses: { gov_tld_bonus: 1.0 },
  stricter_requirements: {},
  overrides: {
    categories: { subject: { en: { added: ['heat pump'], removed: [] } } },
    thresholds: {},
  },
};

function jsonResponse(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function mockFetch({ onPut } = {}) {
  return jest.fn(async (url, options) => {
    const parsed = new URL(String(url));
    if (parsed.pathname === '/api/keywords' && (!options || options.method === undefined)) {
      return jsonResponse(200, KEYWORDS_RESPONSE);
    }
    if (parsed.pathname === '/api/keywords/overrides' && options?.method === 'PUT') {
      if (onPut) return onPut(JSON.parse(options.body));
      return jsonResponse(200, JSON.parse(options.body));
    }
    return jsonResponse(404, {});
  });
}

afterEach(() => {
  jest.restoreAllMocks();
});

const SUBJECT_SUMMARY = /subject \(weight/i;
const CONTEXT_SUMMARY = /context \(weight/i;

async function openCategory(summaryPattern) {
  fireEvent.click(await screen.findByText(summaryPattern));
}

describe('KeywordsPanel rendering', () => {
  it('fetches /api/keywords on mount and renders category blocks', async () => {
    global.fetch = mockFetch();
    render(<KeywordsPanel />);

    await waitFor(() => expect(screen.getByText(SUBJECT_SUMMARY)).toBeInTheDocument());
    expect(screen.getByText(CONTEXT_SUMMARY)).toBeInTheDocument();
  });

  it('shows weight and terms grouped by language inside a category', async () => {
    global.fetch = mockFetch();
    render(<KeywordsPanel />);
    await waitFor(() => expect(screen.getByText(SUBJECT_SUMMARY)).toBeInTheDocument());

    await openCategory(SUBJECT_SUMMARY);

    expect(screen.getByText(/weight 3/)).toBeInTheDocument();
    expect(screen.getByText('waste heat')).toBeInTheDocument();
    expect(screen.getByText('Abwärme')).toBeInTheDocument();
  });

  it('visually marks a custom-added term', async () => {
    global.fetch = mockFetch();
    render(<KeywordsPanel />);
    await waitFor(() => expect(screen.getByText(SUBJECT_SUMMARY)).toBeInTheDocument());
    await openCategory(SUBJECT_SUMMARY);

    const addedTerm = screen.getByText('heat pump');
    expect(addedTerm.className).toEqual(expect.stringContaining('custom'));
  });

  it('shows threshold inputs with current values', async () => {
    global.fetch = mockFetch();
    render(<KeywordsPanel />);
    await waitFor(() => expect(screen.getByText(SUBJECT_SUMMARY)).toBeInTheDocument());

    expect(screen.getByLabelText(/minimum keyword score/i)).toHaveValue(5);
    expect(screen.getByLabelText(/minimum matches/i)).toHaveValue(2);
  });

  it('shows a calibration note pointing at the URL tester', async () => {
    global.fetch = mockFetch();
    render(<KeywordsPanel />);
    await waitFor(() => expect(screen.getByText(SUBJECT_SUMMARY)).toBeInTheDocument());
    expect(screen.getByText(/analyze/i)).toBeInTheDocument();
  });

  it('shows a note that changes apply to future scans', async () => {
    global.fetch = mockFetch();
    render(<KeywordsPanel />);
    await waitFor(() => expect(screen.getByText(SUBJECT_SUMMARY)).toBeInTheDocument());
    expect(screen.getByText(/future scans/i)).toBeInTheDocument();
  });
});

describe('KeywordsPanel add/remove term flows', () => {
  it('adds a term to a category+language via the add-term form', async () => {
    global.fetch = mockFetch();
    render(<KeywordsPanel />);
    await waitFor(() => expect(screen.getByText(SUBJECT_SUMMARY)).toBeInTheDocument());
    await openCategory(SUBJECT_SUMMARY);

    fireEvent.change(screen.getByLabelText(/category/i), { target: { value: 'subject' } });
    fireEvent.change(screen.getByLabelText(/language/i), { target: { value: 'en' } });
    fireEvent.change(screen.getByLabelText(/new term/i), { target: { value: 'thermal recycling' } });
    fireEvent.click(screen.getByRole('button', { name: /add term/i }));

    expect(screen.getByText('thermal recycling')).toBeInTheDocument();
  });

  it('removes a YAML term (strikes it through, offers restore)', async () => {
    global.fetch = mockFetch();
    render(<KeywordsPanel />);
    await waitFor(() => expect(screen.getByText(SUBJECT_SUMMARY)).toBeInTheDocument());
    await openCategory(SUBJECT_SUMMARY);

    const termRow = screen.getByText('waste heat').closest('li');
    fireEvent.click(within(termRow).getByRole('button', { name: /remove/i }));

    expect(within(termRow).getByRole('button', { name: /restore/i })).toBeInTheDocument();
  });

  it('restore un-marks a removed term', async () => {
    global.fetch = mockFetch();
    render(<KeywordsPanel />);
    await waitFor(() => expect(screen.getByText(SUBJECT_SUMMARY)).toBeInTheDocument());
    await openCategory(SUBJECT_SUMMARY);

    const termRow = screen.getByText('waste heat').closest('li');
    fireEvent.click(within(termRow).getByRole('button', { name: /remove/i }));
    fireEvent.click(within(termRow).getByRole('button', { name: /restore/i }));

    expect(within(termRow).getByRole('button', { name: /remove/i })).toBeInTheDocument();
  });

  it('removes a custom-added term entirely', async () => {
    global.fetch = mockFetch();
    render(<KeywordsPanel />);
    await waitFor(() => expect(screen.getByText(SUBJECT_SUMMARY)).toBeInTheDocument());
    await openCategory(SUBJECT_SUMMARY);

    const termRow = screen.getByText('heat pump').closest('li');
    fireEvent.click(within(termRow).getByRole('button', { name: /remove/i }));

    expect(screen.queryByText('heat pump')).not.toBeInTheDocument();
  });
});

describe('KeywordsPanel save', () => {
  it('PUTs the accumulated overrides body on Save', async () => {
    global.fetch = mockFetch();
    render(<KeywordsPanel />);
    await waitFor(() => expect(screen.getByText(SUBJECT_SUMMARY)).toBeInTheDocument());
    await openCategory(SUBJECT_SUMMARY);

    fireEvent.change(screen.getByLabelText(/category/i), { target: { value: 'subject' } });
    fireEvent.change(screen.getByLabelText(/language/i), { target: { value: 'en' } });
    fireEvent.change(screen.getByLabelText(/new term/i), { target: { value: 'thermal recycling' } });
    fireEvent.click(screen.getByRole('button', { name: /add term/i }));

    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => {
      const putCall = global.fetch.mock.calls.find(
        ([url, options]) => String(url).includes('/api/keywords/overrides') && options?.method === 'PUT',
      );
      expect(putCall).toBeTruthy();
      const body = JSON.parse(putCall[1].body);
      expect(body.categories.subject.en.added).toEqual(
        expect.arrayContaining(['heat pump', 'thermal recycling']),
      );
      // A term-only save must NOT pin the current effective thresholds as
      // an override - that would freeze YAML's values forever.
      expect(body.thresholds).toEqual({});
    });
  });

  it('includes thresholds in the PUT only after an explicit edit, and reset clears them', async () => {
    global.fetch = mockFetch();
    render(<KeywordsPanel />);
    await waitFor(() => expect(screen.getByText(SUBJECT_SUMMARY)).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/minimum keyword score/i), { target: { value: '7' } });
    expect(screen.getByText(/Thresholds are overridden/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => {
      const putCall = global.fetch.mock.calls.find(
        ([url, options]) => String(url).includes('/api/keywords/overrides') && options?.method === 'PUT',
      );
      expect(JSON.parse(putCall[1].body).thresholds).toEqual({ minimum_keyword_score: 7 });
    });

    fireEvent.click(screen.getByRole('button', { name: /reset to yaml defaults/i }));
    expect(screen.queryByText(/Thresholds are overridden/)).not.toBeInTheDocument();
    // Input falls back to the effective (YAML) value after the reset.
    expect(screen.getByLabelText(/minimum keyword score/i)).toHaveValue(5);
  });

  it('shows a validation error returned by the PUT', async () => {
    global.fetch = mockFetch({
      onPut: () => jsonResponse(422, { detail: ["Unknown category: 'bogus'"] }),
    });
    render(<KeywordsPanel />);
    await waitFor(() => expect(screen.getByText(SUBJECT_SUMMARY)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/unknown category/i));
  });
});
