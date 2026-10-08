import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { ApiError, schedulePost } from "../api";
import { formatTimestamp, localDateTimeValue } from "../format";

const IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/gif"]);
const MAX_IMAGE_BYTES = 5_242_880;

export function SchedulePage() {
  const minimum = useMemo(() => localDateTimeValue(new Date()), []);
  const [content, setContent] = useState("");
  const [scheduledLocal, setScheduledLocal] = useState("");
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const imageInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!imageFile) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(imageFile);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [imageFile]);

  function clearImage() {
    setImageFile(null);
    if (imageInput.current) {
      imageInput.current.value = "";
    }
  }

  function onImageChange(file: File | null) {
    setError(null);
    if (!file) {
      setImageFile(null);
      return;
    }
    if (!IMAGE_TYPES.has(file.type)) {
      clearImage();
      setError("Choose a JPEG, PNG, or GIF.");
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      clearImage();
      setError("Image must be 5 MB or smaller.");
      return;
    }
    setImageFile(file);
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setConfirmation(null);
    const trimmed = content.trim();
    if (!trimmed) {
      setError("Post text is required.");
      return;
    }
    if (trimmed.length > 3000) {
      setError("Post text must be at most 3000 characters.");
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
      const image = imageFile ? await readImage(imageFile) : undefined;
      const created = await schedulePost(trimmed, scheduled.toISOString(), image);
      setConfirmation(`Scheduled for ${formatTimestamp(created.scheduled_at)}.`);
      setContent("");
      setScheduledLocal("");
      clearImage();
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.message : "Could not schedule the post");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="narrow">
      <h1>Schedule</h1>
      <p className="lede">Write the post, add an image if you want, and choose when it goes live.</p>
      {confirmation ? <p className="banner success">{confirmation}</p> : null}
      {error ? <p className="banner error">{error}</p> : null}
      <form className="card form" onSubmit={onSubmit}>
        <label htmlFor="content">Post text</label>
        <textarea
          id="content"
          value={content}
          onChange={(event) => setContent(event.target.value)}
          rows={6}
          maxLength={3000}
          placeholder="What do you want to publish?"
        />
        <label htmlFor="image">Image</label>
        <input
          id="image"
          ref={imageInput}
          type="file"
          accept="image/jpeg,image/png,image/gif"
          onChange={(event) => onImageChange(event.target.files?.[0] ?? null)}
        />
        {previewUrl ? (
          <div className="preview">
            <img src={previewUrl} alt="Selected post image" />
            <button className="button secondary" type="button" onClick={clearImage}>
              Remove image
            </button>
          </div>
        ) : (
          <p className="hint">Optional. JPEG, PNG, or GIF, up to 5 MB. It appears on your profile with the text.</p>
        )}
        <label htmlFor="scheduled-at">Date and time</label>
        <input
          id="scheduled-at"
          type="datetime-local"
          min={minimum}
          value={scheduledLocal}
          onChange={(event) => setScheduledLocal(event.target.value)}
        />
        <button className="button" type="submit" disabled={submitting}>
          {submitting ? "Scheduling…" : "Schedule post"}
        </button>
      </form>
    </section>
  );
}

function readImage(file: File): Promise<{ mediaType: string; base64: string }> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      const comma = result.indexOf(",");
      resolve({ mediaType: file.type, base64: comma >= 0 ? result.slice(comma + 1) : result });
    };
    reader.onerror = () => reject(new Error("Could not read the image"));
    reader.readAsDataURL(file);
  });
}
