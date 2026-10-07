import { useMemo, useState, type FormEvent } from "react";

import { ApiError, schedulePost } from "../api";
import { formatTimestamp, localDateTimeValue } from "../format";

export function SchedulePage() {
  const minimum = useMemo(() => localDateTimeValue(new Date()), []);
  const [content, setContent] = useState("");
  const [scheduledLocal, setScheduledLocal] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setConfirmation(null);
    const trimmed = content.trim();
    if (!trimmed) {
      setError("Post content is required.");
      return;
    }
    if (trimmed.length > 3000) {
      setError("Post content must be at most 3000 characters.");
      return;
    }
    if (!scheduledLocal) {
      setError("A future date and time is required.");
      return;
    }
    const scheduled = new Date(scheduledLocal);
    if (Number.isNaN(scheduled.getTime()) || scheduled.getTime() <= Date.now()) {
      setError("Choose a date and time in the future.");
      return;
    }
    setSubmitting(true);
    try {
      const created = await schedulePost(trimmed, scheduled.toISOString());
      setConfirmation(`Scheduled for ${formatTimestamp(created.scheduled_at)}.`);
      setContent("");
      setScheduledLocal("");
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.message : "Could not schedule the post");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="narrow">
      <h1>Schedule Post</h1>
      <p className="lede">The browser sends the text and a timezone-aware time. The worker publishes it later.</p>
      {confirmation ? <p className="banner success">{confirmation}</p> : null}
      {error ? <p className="banner error">{error}</p> : null}
      <form className="card form" onSubmit={onSubmit}>
        <label htmlFor="content">Post content</label>
        <textarea
          id="content"
          value={content}
          onChange={(event) => setContent(event.target.value)}
          rows={6}
          maxLength={3000}
          placeholder="What do you want to publish?"
        />
        <label htmlFor="scheduled-at">Scheduled date/time</label>
        <input
          id="scheduled-at"
          type="datetime-local"
          min={minimum}
          value={scheduledLocal}
          onChange={(event) => setScheduledLocal(event.target.value)}
        />
        <button className="button" type="submit" disabled={submitting}>
          {submitting ? "Scheduling…" : "Schedule Post"}
        </button>
      </form>
    </section>
  );
}
