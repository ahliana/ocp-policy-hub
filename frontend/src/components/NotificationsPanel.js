import React, { useEffect, useState } from 'react';
import { apiUrl } from '../config/api';
import { adminHeaders } from '../utils/adminAuth';
import HelpNote from './HelpNote';

// What a subscription can hear about, and how it can be told. Machine
// values are what the API contract uses (topics/frequency); labels are the
// plain-language text shown in the table and the add form.
const TOPICS = [
    { value: 'early_signals', label: 'Early signals' },
    { value: 'ops_alerts', label: 'Operational alerts' },
];

const FREQUENCIES = [
    { value: 'immediate', label: 'Immediately' },
    { value: 'daily', label: 'Daily digest' },
    { value: 'weekly', label: 'Weekly digest' },
];

function topicLabel(topic) {
    return TOPICS.find((t) => t.value === topic)?.label || topic;
}

function frequencyLabel(frequency) {
    return FREQUENCIES.find((f) => f.value === frequency)?.label || frequency;
}

function hearsAbout(topics) {
    return (topics || []).map(topicLabel).join(' + ');
}

async function fetchSubscriptions() {
    const response = await fetch(apiUrl('/api/notifications/subscriptions'), { headers: adminHeaders() });
    if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Subscriptions request failed (${response.status})`);
    }
    return response.json();
}

async function fetchStatus() {
    const response = await fetch(apiUrl('/api/notifications/status'), { headers: adminHeaders() });
    if (!response.ok) {
        throw new Error(`Status request failed (${response.status})`);
    }
    return response.json();
}

async function postSubscription(body) {
    const response = await fetch(apiUrl('/api/notifications/subscriptions'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...adminHeaders() },
        body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(data.detail || `Add failed (${response.status})`);
    }
    return data;
}

async function deleteSubscription(id) {
    const response = await fetch(apiUrl(`/api/notifications/subscriptions/${id}`), {
        method: 'DELETE',
        headers: adminHeaders(),
    });
    if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Remove failed (${response.status})`);
    }
    return response.json();
}

function emptyForm() {
    return {
        email: '',
        topics: { early_signals: false, ops_alerts: false },
        frequency: 'immediate',
    };
}

// Notifications panel (WP-44) - lets an admin subscribe an email address to
// early signals and/or operational alerts, at an immediate, daily, or
// weekly cadence. Lives in the admin area, between Schedules and How it
// works. The email-sending status (configured or not, last send error) is
// supplementary - if that fetch fails the panel still works, it just shows
// no status line.
function NotificationsPanel() {
    const [subscriptions, setSubscriptions] = useState([]);
    const [status, setStatus] = useState(null);
    const [loadError, setLoadError] = useState('');
    const [actionError, setActionError] = useState('');
    const [formError, setFormError] = useState('');
    const [form, setForm] = useState(emptyForm());

    const loadSubscriptions = () => fetchSubscriptions()
        .then((data) => setSubscriptions(data.subscriptions || []))
        .catch((err) => setLoadError(err.message));

    const loadStatus = () => fetchStatus()
        .then((data) => setStatus(data))
        .catch(() => {});

    useEffect(() => {
        loadSubscriptions();
        loadStatus();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const handleTopicToggle = (topic) => {
        setForm((current) => ({
            ...current,
            topics: { ...current.topics, [topic]: !current.topics[topic] },
        }));
    };

    const handleAdd = async () => {
        setFormError('');
        const email = form.email.trim();
        const topics = TOPICS.filter((t) => form.topics[t.value]).map((t) => t.value);
        if (!email.includes('@')) {
            setFormError('Enter a valid email address.');
            return;
        }
        if (topics.length === 0) {
            setFormError('Select at least one topic.');
            return;
        }
        try {
            await postSubscription({ email, topics, frequency: form.frequency });
            setForm(emptyForm());
            await loadSubscriptions();
        } catch (err) {
            setFormError(err.message);
        }
    };

    const handleRemove = async (subscription) => {
        setActionError('');
        try {
            await deleteSubscription(subscription.id);
            await loadSubscriptions();
        } catch (err) {
            setActionError(err.message);
        }
    };

    return (
        <div className="notifications-panel" aria-label="Notifications panel">
            <h2 className="panel-heading">Email notifications</h2>
            <HelpNote label="How notifications work" className="notifications-help-note">
                <p>
                    Add an email address and pick what it should hear about: early signals are
                    brand-new finds that are still proposals, and operational alerts are problems
                    like a failed feed or a scan that hit its spending cap. Immediate sends one
                    email as things happen (never more than one an hour per subject); daily and
                    weekly send one tidy digest instead. Nothing is sent until email sending is
                    set up.
                </p>
            </HelpNote>

            {status && status.smtp_configured === false && (
                <p className="notifications-smtp-note" role="note">
                    Email sending is not set up yet - subscriptions are saved and digests will
                    start once it is.
                </p>
            )}
            {status && status.last_send_error && (
                <p className="notifications-send-error" role="alert">
                    {`The last email could not be sent: ${status.last_send_error}`}
                </p>
            )}

            {loadError && <p role="alert">{loadError}</p>}
            {actionError && <p role="alert">{actionError}</p>}

            <div className="admin-table-wrap">
                {subscriptions.length === 0 ? (
                    <p className="notifications-empty" role="note">Nobody is subscribed yet.</p>
                ) : (
                    <table className="notifications-table">
                        <thead>
                            <tr>
                                <th>Email</th>
                                <th>Hears about</th>
                                <th>How often</th>
                                <th>Remove</th>
                            </tr>
                        </thead>
                        <tbody>
                            {subscriptions.map((subscription) => (
                                <tr key={subscription.id}>
                                    <td>{subscription.email}</td>
                                    <td>{hearsAbout(subscription.topics)}</td>
                                    <td>{frequencyLabel(subscription.frequency)}</td>
                                    <td>
                                        <button
                                            type="button"
                                            className="button"
                                            onClick={() => handleRemove(subscription)}
                                        >
                                            Remove
                                        </button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            <div className="notifications-add-form">
                <label htmlFor="notifications-email">Email address</label>
                <input
                    id="notifications-email"
                    type="email"
                    value={form.email}
                    onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))}
                />

                <fieldset>
                    <legend>Topics</legend>
                    {TOPICS.map((topic) => (
                        <label key={topic.value} htmlFor={`notifications-topic-${topic.value}`}>
                            <input
                                id={`notifications-topic-${topic.value}`}
                                type="checkbox"
                                checked={form.topics[topic.value]}
                                onChange={() => handleTopicToggle(topic.value)}
                            />
                            {topic.label}
                        </label>
                    ))}
                </fieldset>

                <label htmlFor="notifications-frequency">How often</label>
                <select
                    id="notifications-frequency"
                    value={form.frequency}
                    onChange={(event) => setForm((current) => ({ ...current, frequency: event.target.value }))}
                >
                    {FREQUENCIES.map((frequency) => (
                        <option key={frequency.value} value={frequency.value}>{frequency.label}</option>
                    ))}
                </select>

                {formError && <p role="alert">{formError}</p>}
                <button type="button" className="button" onClick={handleAdd}>
                    Add
                </button>
            </div>
        </div>
    );
}

export default NotificationsPanel;
