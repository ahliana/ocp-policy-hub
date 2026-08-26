import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import AskPolicyBox from './AskPolicyBox';

describe('AskPolicyBox busy feedback', () => {
  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  it('shows a live ticking status while the question is being answered', async () => {
    jest.useFakeTimers();
    let resolveFetch;
    global.fetch = jest.fn(() => new Promise((resolve) => { resolveFetch = resolve; }));

    const { fireEvent, render, screen, act } = require('@testing-library/react');
    const AskPolicyBoxComponent = require('./AskPolicyBox').default;
    render(<AskPolicyBoxComponent />);

    fireEvent.change(screen.getByPlaceholderText(/Ask about discovered policies/), {
      target: { value: 'What does Sweden require?' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
    });

    expect(screen.getByRole('status')).toHaveTextContent('Searching the policy library');
    await act(async () => {
      jest.advanceTimersByTime(3000);
    });
    expect(screen.getByRole('status')).toHaveTextContent('3s');

    await act(async () => {
      resolveFetch({ ok: true, json: async () => ({ answer: 'Sweden requires reporting.' }) });
    });
    expect(screen.getByText(/Sweden requires reporting/)).toBeInTheDocument();
  });
});

describe('AskPolicyBox markdown rendering', () => {
  beforeEach(() => {
    global.fetch = jest.fn();
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('renders Markdown headings and bold as formatted elements, not literal syntax', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ answer: '## Germany\n**Heat reuse** is mandatory.' }),
    });

    render(<AskPolicyBox />);
    fireEvent.change(screen.getByPlaceholderText(/ask about discovered policies/i), {
      target: { value: 'What does Germany require?' },
    });
    fireEvent.click(screen.getByRole('button', { name: /ask/i }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 2, name: 'Germany' })).toBeInTheDocument();
    });
    expect(screen.getByText('Heat reuse').tagName).toBe('STRONG');
    expect(screen.queryByText(/##/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\*\*/)).not.toBeInTheDocument();
  });
});

describe('AskPolicyBox', () => {
  beforeEach(() => {
    global.fetch = jest.fn();
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('submits a question and shows the answer', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ answer: 'Germany requires heat reuse plans.', remaining_today: 10 }),
    });

    render(<AskPolicyBox />);
    fireEvent.change(screen.getByPlaceholderText(/ask about discovered policies/i), {
      target: { value: 'What does Germany require?' },
    });
    fireEvent.click(screen.getByRole('button', { name: /ask/i }));

    await waitFor(() => {
      expect(screen.getByText(/Germany requires heat reuse plans\./)).toBeInTheDocument();
    });
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/ask'),
      expect.objectContaining({ method: 'POST' })
    );
  });

  it('shows the server detail message on 429', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 429,
      json: async () => ({ detail: 'The daily question limit has been reached.' }),
    });

    render(<AskPolicyBox />);
    fireEvent.change(screen.getByPlaceholderText(/ask about discovered policies/i), {
      target: { value: 'Anything in France?' },
    });
    fireEvent.click(screen.getByRole('button', { name: /ask/i }));

    await waitFor(() => {
      expect(screen.getByText(/daily question limit/i)).toBeInTheDocument();
    });
  });

  it('disables the button while empty', () => {
    render(<AskPolicyBox />);
    expect(screen.getByRole('button', { name: /ask/i })).toBeDisabled();
  });

  it('shows the multilingual hint alongside the ask box', () => {
    render(<AskPolicyBox />);
    expect(screen.getByText(/Ask in any/i)).toBeInTheDocument();
  });

  it('renders a calm info note (not an error) when the service is unconfigured (503)', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 503,
      json: async () => ({ detail: 'The question service is not configured yet.' }),
    });

    render(<AskPolicyBox />);
    fireEvent.change(screen.getByPlaceholderText(/ask about discovered policies/i), {
      target: { value: 'What does France require?' },
    });
    fireEvent.click(screen.getByRole('button', { name: /ask/i }));

    await waitFor(() => {
      expect(screen.getByText(/not configured yet/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/You can still browse every found policy below/i)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('renders a calm info note when questions are temporarily disabled (503)', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 503,
      json: async () => ({ detail: 'Questions are temporarily disabled by the administrator.' }),
    });

    render(<AskPolicyBox />);
    fireEvent.change(screen.getByPlaceholderText(/ask about discovered policies/i), {
      target: { value: 'What does Japan require?' },
    });
    fireEvent.click(screen.getByRole('button', { name: /ask/i }));

    await waitFor(() => {
      expect(screen.getByText(/temporarily disabled/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('keeps error styling for a real failure (500)', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      json: async () => ({ detail: 'Something went wrong answering your question.' }),
    });

    render(<AskPolicyBox />);
    fireEvent.change(screen.getByPlaceholderText(/ask about discovered policies/i), {
      target: { value: 'What does Brazil require?' },
    });
    fireEvent.click(screen.getByRole('button', { name: /ask/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/went wrong/i);
    });
  });
});
