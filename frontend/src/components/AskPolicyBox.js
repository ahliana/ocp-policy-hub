import React, { useEffect, useRef, useState } from 'react';
import { apiUrl } from '../config/api';
import { renderMiniMarkdown } from '../utils/miniMarkdown';

function AnswerText({ answer }) {
  return (
    <div className="ask-box-answer" role="region" aria-label="Answer">
      {renderMiniMarkdown(answer)}
    </div>
  );
}

function AskPolicyBox() {
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [isAsking, setIsAsking] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const answerRef = useRef(null);

  useEffect(() => {
    if (!isAsking) return undefined;
    setElapsedSeconds(0);
    // A visibly ticking clock proves the request is alive; answers
    // typically take 5-30 seconds while the library is searched.
    const timer = setInterval(() => {
      setElapsedSeconds((prev) => prev + 1);
    }, 1000);
    return () => clearInterval(timer);
  }, [isAsking]);

  useEffect(() => {
    if (answer && answerRef.current) {
      answerRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [answer]);

  const submitQuestion = async (event) => {
    event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || isAsking) return;

    setIsAsking(true);
    setError('');
    setNotice('');
    setAnswer('');

    try {
      const response = await fetch(apiUrl('/api/ask'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: trimmed }),
      });

      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        const detail = body.detail
          || 'The question could not be answered right now. Please try again.';
        if (response.status === 503) {
          // Service not configured or temporarily disabled by the admin -
          // not a failure, so this stays calm rather than reading as an error.
          setNotice(`${detail} You can still browse every found policy below.`);
        } else {
          setError(detail);
        }
        return;
      }

      const data = await response.json();
      setAnswer(data.answer || 'No answer was returned. Try rephrasing your question.');
    } catch (askError) {
      setError(askError.message);
    } finally {
      setIsAsking(false);
    }
  };

  return (
    <section className="ask-box" aria-label="Ask about policies">
      <h2 className="ask-box-title">Ask about found policies</h2>
      <p className="ask-box-hint">
        Free for everyone - answers only from what has already been found. Ask in any
        language, e.g. &quot;What does Germany require for data center waste heat?&quot;
      </p>
      <form className="ask-box-form" onSubmit={submitQuestion}>
        <label className="visually-hidden" htmlFor="ask-box-input">
          Ask about policies
        </label>
        <input
          id="ask-box-input"
          className="ask-box-input"
          type="text"
          value={question}
          maxLength={500}
          placeholder="Ask about discovered policies in any region..."
          onChange={(event) => setQuestion(event.target.value)}
          disabled={isAsking}
        />
        <button
          type="submit"
          className="ask-box-button"
          disabled={isAsking || question.trim().length < 3}
        >
          {isAsking ? 'Searching...' : 'Ask'}
        </button>
      </form>
      {isAsking ? (
        <p className="ask-box-status ask-box-status-busy" role="status">
          <span className="search-pulse-dot" aria-hidden="true" />
          Searching the policy library...
          {/* The ticking counter is visual-only; inside the live region it
              would make screen readers re-announce every second. */}
          <span aria-hidden="true"> {elapsedSeconds}s</span>
        </p>
      ) : null}
      {notice ? (
        <p className="ask-box-info" role="status">
          {notice}
        </p>
      ) : null}
      {error ? (
        <p className="ask-box-error" role="alert">
          {error}
        </p>
      ) : null}
      {answer ? <div ref={answerRef}><AnswerText answer={answer} /></div> : null}
    </section>
  );
}

export default AskPolicyBox;
