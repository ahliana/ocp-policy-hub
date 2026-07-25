import React, { useEffect, useMemo, useState } from 'react';
import { apiUrl } from '../config/api';
import { adminHeaders } from '../utils/adminAuth';

// Channels a schedule can fire through ScanManager for. "news" is
// deliberately excluded here - it runs through its own runner outside
// ScanManager entirely (see src/orchestration/scan_manager.py's
// _domain_channel and src/storage/seed_schedules.py's module docstring),
// so a schedules row scoped to "news" alone could never actually fire
// anything.
const CHANNELS = ['crawl', 'law_apis', 'transposition'];

const DOW_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

const HOURS = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, '0'));
const MINUTES = ['00', '15', '30', '45'];

function ordinal(n) {
    const rem100 = n % 100;
    if (rem100 >= 11 && rem100 <= 13) return `${n}th`;
    switch (n % 10) {
        case 1: return `${n}st`;
        case 2: return `${n}nd`;
        case 3: return `${n}rd`;
        default: return `${n}th`;
    }
}

// Cadence-in-words - "weekly:<dow>:<HH>:<MM>" / "monthly:<dom>:<HH>:<MM>"
// (UTC) rendered as "Mondays 06:30 UTC" / "1st of the month 06:00 UTC".
export function formatCadence(cadence) {
    const parts = (cadence || '').split(':');
    if (parts.length !== 4) return cadence || '';
    const [type, value, hour, minute] = parts;
    const time = `${hour.padStart(2, '0')}:${minute.padStart(2, '0')} UTC`;
    const n = Number(value);
    if (type === 'weekly') {
        const name = DOW_NAMES[n];
        return name ? `${name}s ${time}` : cadence;
    }
    if (type === 'monthly') {
        return `${ordinal(n)} of the month ${time}`;
    }
    return cadence;
}

function formatUsd(value) {
    return value == null ? '-' : `$${Number(value).toFixed(2)}`;
}

function formatDateTime(value) {
    return value ? value.replace('T', ' ') : '-';
}

async function fetchSchedules() {
    const response = await fetch(apiUrl('/api/schedules'), { headers: adminHeaders() });
    if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Schedules request failed (${response.status})`);
    }
    return response.json();
}

async function postSchedule(body) {
    const response = await fetch(apiUrl('/api/schedules'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...adminHeaders() },
        body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        const detail = Array.isArray(data.detail) ? data.detail.join('; ') : data.detail;
        throw new Error(detail || `Create failed (${response.status})`);
    }
    return data;
}

async function putSchedule(id, body) {
    const response = await fetch(apiUrl(`/api/schedules/${id}`), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...adminHeaders() },
        body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        const detail = Array.isArray(data.detail) ? data.detail.join('; ') : data.detail;
        throw new Error(detail || `Update failed (${response.status})`);
    }
    return data;
}

async function deleteSchedule(id) {
    const response = await fetch(apiUrl(`/api/schedules/${id}`), {
        method: 'DELETE',
        headers: adminHeaders(),
    });
    if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Delete failed (${response.status})`);
    }
    return response.json();
}

async function runScheduleNow(id) {
    const response = await fetch(apiUrl(`/api/schedules/${id}/run-now`), {
        method: 'POST',
        headers: adminHeaders(),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(data.detail || `Run now failed (${response.status})`);
    }
    return data;
}

function emptyForm() {
    return {
        name: '',
        domains: '',
        channels: { crawl: false, law_apis: false, transposition: false },
        deep: false,
        cadenceType: 'weekly',
        day: '0',
        hour: '06',
        minute: '00',
        ceiling: '',
    };
}

function buildCadence(form) {
    const day = form.cadenceType === 'weekly' ? form.day : String(Number(form.day));
    return `${form.cadenceType}:${day}:${form.hour}:${form.minute}`;
}

function buildCreateBody(form) {
    return {
        name: form.name.trim(),
        domains: form.domains.trim(),
        channels: CHANNELS.filter((c) => form.channels[c]),
        deep: form.deep,
        cadence: buildCadence(form),
        monthly_ceiling_usd: form.ceiling === '' ? null : Number(form.ceiling),
    };
}

// Inverse of buildCadence() - populates the form when editing an existing
// schedule. Falls back to sane defaults if a schedule somehow has a
// cadence the form's selects can't represent exactly.
function parseCadence(cadence) {
    const parts = (cadence || '').split(':');
    if (parts.length !== 4) return { cadenceType: 'weekly', day: '0', hour: '06', minute: '00' };
    const [type, day, hour, minute] = parts;
    return { cadenceType: type, day, hour, minute };
}

function formToEdit(schedule) {
    const { cadenceType, day, hour, minute } = parseCadence(schedule.cadence);
    return {
        name: schedule.name,
        domains: schedule.domains,
        channels: {
            crawl: (schedule.channels || []).includes('crawl'),
            law_apis: (schedule.channels || []).includes('law_apis'),
            transposition: (schedule.channels || []).includes('transposition'),
        },
        deep: !!schedule.deep,
        cadenceType,
        day,
        hour,
        minute,
        ceiling: schedule.monthly_ceiling_usd == null ? '' : String(schedule.monthly_ceiling_usd),
    };
}

// Schedules panel (WP-11) - in-app scheduled scans. Lists every schedule
// with its cadence in plain words, last/next run, expected per-run and
// per-month cost, and (when a monthly ceiling has paused it) a paused
// badge with the reason. Lives in the admin area, below the Keywords
// panel. Create/edit/delete/run-now all go through adminHeaders().
function SchedulesPanel() {
    const [schedules, setSchedules] = useState([]);
    const [loadError, setLoadError] = useState('');
    const [actionError, setActionError] = useState('');
    const [createError, setCreateError] = useState('');
    const [form, setForm] = useState(emptyForm());
    const [editingId, setEditingId] = useState(null);
    const [confirmDeleteId, setConfirmDeleteId] = useState(null);

    const load = () => fetchSchedules()
        .then((data) => setSchedules(data.schedules || []))
        .catch((err) => setLoadError(err.message));

    useEffect(() => {
        load();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const dayOptions = useMemo(() => {
        if (form.cadenceType === 'weekly') {
            return DOW_NAMES.map((label, index) => ({ value: String(index), label }));
        }
        return Array.from({ length: 31 }, (_, i) => i + 1).map((n) => (
            { value: String(n), label: ordinal(n) }
        ));
    }, [form.cadenceType]);

    const handleChannelToggle = (channel) => {
        setForm((current) => ({
            ...current,
            channels: { ...current.channels, [channel]: !current.channels[channel] },
        }));
    };

    const handleSubmit = async () => {
        setCreateError('');
        if (!CHANNELS.some((c) => form.channels[c])) {
            setCreateError('Select at least one source channel.');
            return;
        }
        try {
            if (editingId) {
                await putSchedule(editingId, buildCreateBody(form));
            } else {
                await postSchedule(buildCreateBody(form));
            }
            setForm(emptyForm());
            setEditingId(null);
            await load();
        } catch (err) {
            setCreateError(err.message);
        }
    };

    const handleEdit = (schedule) => {
        setCreateError('');
        setEditingId(schedule.id);
        setForm(formToEdit(schedule));
    };

    const handleCancelEdit = () => {
        setCreateError('');
        setEditingId(null);
        setForm(emptyForm());
    };

    const handleToggleEnabled = async (schedule) => {
        setActionError('');
        const previous = schedules;
        const nextEnabled = !schedule.enabled;
        setSchedules((current) => current.map((s) => (
            s.id === schedule.id ? { ...s, enabled: nextEnabled } : s
        )));
        try {
            await putSchedule(schedule.id, { enabled: nextEnabled });
        } catch (err) {
            setSchedules(previous);
            setActionError(err.message);
        }
    };

    const handleRunNow = async (schedule) => {
        setActionError('');
        try {
            await runScheduleNow(schedule.id);
            await load();
        } catch (err) {
            setActionError(err.message);
        }
    };

    const handleDelete = async (schedule) => {
        setActionError('');
        try {
            await deleteSchedule(schedule.id);
            setConfirmDeleteId(null);
            await load();
        } catch (err) {
            setActionError(err.message);
        }
    };

    return (
        <div className="schedules-panel" aria-label="Schedules panel">
            <h2 className="panel-heading">Schedules - in-app scheduled scans</h2>
            {loadError && <p role="alert">{loadError}</p>}
            {actionError && <p role="alert">{actionError}</p>}

            <table className="schedules-table">
                <thead>
                    <tr>
                        <th>Name</th>
                        <th>Scope</th>
                        <th>Cadence</th>
                        <th>Enabled</th>
                        <th>Last run</th>
                        <th>Next run</th>
                        <th>Per run</th>
                        <th>Per month</th>
                        <th>Ceiling</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {schedules.map((schedule) => (
                        <tr key={schedule.id}>
                            <td>{schedule.name}</td>
                            <td>{schedule.domains}</td>
                            <td>{formatCadence(schedule.cadence)}</td>
                            <td>
                                <button
                                    type="button"
                                    className="button"
                                    aria-label={`Toggle ${schedule.name}`}
                                    onClick={() => handleToggleEnabled(schedule)}
                                >
                                    {schedule.enabled ? 'Enabled' : 'Disabled'}
                                </button>
                            </td>
                            <td>
                                {schedule.last_scan_id
                                    ? `${schedule.last_scan_id} @ ${formatDateTime(schedule.last_run_at)}`
                                    : 'Never run'}
                            </td>
                            <td>{formatDateTime(schedule.next_run_at)}</td>
                            <td>{formatUsd(schedule.estimate_usd)}</td>
                            <td>{formatUsd(schedule.per_month_usd)}</td>
                            <td>
                                {formatUsd(schedule.monthly_ceiling_usd)}
                                {schedule.paused_reason && (
                                    <>
                                        {' '}
                                        <span className="schedule-paused-badge">Paused</span>
                                        <p role="note" className="schedule-paused-reason">
                                            {schedule.paused_reason}
                                        </p>
                                    </>
                                )}
                            </td>
                            <td>
                                <button
                                    type="button"
                                    className="button"
                                    onClick={() => handleRunNow(schedule)}
                                >
                                    Run now
                                </button>
                                <button
                                    type="button"
                                    className="button"
                                    onClick={() => handleEdit(schedule)}
                                >
                                    Edit
                                </button>
                                {confirmDeleteId === schedule.id ? (
                                    <>
                                        <button
                                            type="button"
                                            className="button"
                                            onClick={() => handleDelete(schedule)}
                                        >
                                            Confirm delete
                                        </button>
                                        <button
                                            type="button"
                                            className="button"
                                            onClick={() => setConfirmDeleteId(null)}
                                        >
                                            Cancel
                                        </button>
                                    </>
                                ) : (
                                    <button
                                        type="button"
                                        className="button"
                                        onClick={() => setConfirmDeleteId(schedule.id)}
                                    >
                                        Delete
                                    </button>
                                )}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>

            <div className="schedules-create-form">
                <h3>{editingId ? 'Edit schedule' : 'New schedule'}</h3>
                <label htmlFor="schedule-name">Schedule name</label>
                <input
                    id="schedule-name"
                    type="text"
                    value={form.name}
                    onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
                />

                <label htmlFor="schedule-scope">Scope (domains/group)</label>
                <input
                    id="schedule-scope"
                    type="text"
                    value={form.domains}
                    onChange={(event) => setForm((current) => ({ ...current, domains: event.target.value }))}
                />

                <fieldset>
                    <legend>Channels</legend>
                    {CHANNELS.map((channel) => (
                        <label key={channel} htmlFor={`schedule-channel-${channel}`}>
                            <input
                                id={`schedule-channel-${channel}`}
                                type="checkbox"
                                checked={form.channels[channel]}
                                onChange={() => handleChannelToggle(channel)}
                            />
                            {channel}
                        </label>
                    ))}
                </fieldset>

                <label htmlFor="schedule-deep">
                    <input
                        id="schedule-deep"
                        type="checkbox"
                        checked={form.deep}
                        onChange={(event) => setForm((current) => ({ ...current, deep: event.target.checked }))}
                    />
                    Deep scan
                </label>

                <label htmlFor="schedule-cadence-type">Cadence type</label>
                <select
                    id="schedule-cadence-type"
                    value={form.cadenceType}
                    onChange={(event) => setForm((current) => ({
                        ...current,
                        cadenceType: event.target.value,
                        day: event.target.value === 'weekly' ? '0' : '1',
                    }))}
                >
                    <option value="weekly">Weekly</option>
                    <option value="monthly">Monthly</option>
                </select>

                <label htmlFor="schedule-day">Day</label>
                <select
                    id="schedule-day"
                    value={form.day}
                    onChange={(event) => setForm((current) => ({ ...current, day: event.target.value }))}
                >
                    {dayOptions.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                </select>

                <label htmlFor="schedule-hour">Hour</label>
                <select
                    id="schedule-hour"
                    value={form.hour}
                    onChange={(event) => setForm((current) => ({ ...current, hour: event.target.value }))}
                >
                    {HOURS.map((hour) => (
                        <option key={hour} value={hour}>{hour}</option>
                    ))}
                </select>

                <label htmlFor="schedule-minute">Minute</label>
                <select
                    id="schedule-minute"
                    value={form.minute}
                    onChange={(event) => setForm((current) => ({ ...current, minute: event.target.value }))}
                >
                    {MINUTES.map((minute) => (
                        <option key={minute} value={minute}>{minute}</option>
                    ))}
                </select>

                <label htmlFor="schedule-ceiling">Monthly ceiling (USD)</label>
                <input
                    id="schedule-ceiling"
                    type="number"
                    value={form.ceiling}
                    onChange={(event) => setForm((current) => ({ ...current, ceiling: event.target.value }))}
                />

                {createError && <p role="alert">{createError}</p>}
                <button type="button" className="button" onClick={handleSubmit}>
                    {editingId ? 'Save changes' : 'Create schedule'}
                </button>
                {editingId && (
                    <button type="button" className="button" onClick={handleCancelEdit}>
                        Cancel edit
                    </button>
                )}
            </div>

            <p className="schedules-note" role="note">
                Cadence is UTC, weekly (day + time) or monthly (day-of-month + time) - a monthly
                ceiling pauses a schedule until spend resets at the next calendar month; it never
                disables it.
            </p>
        </div>
    );
}

export default SchedulesPanel;
