import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, deletePost, listPosts, type ScheduledPost } from "../api";
import { formatTimestamp } from "../format";

export function PostsPage() {
  const [posts, setPosts] = useState<ScheduledPost[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    listPosts()
      .then(setPosts)
      .catch((reason: unknown) => {
        setPosts([]);
        setError(reason instanceof ApiError ? reason.message : "Could not load posts");
      })
      .finally(() => setLoading(false));
  }, []);

  async function confirmDelete(post: ScheduledPost) {
    setError(null);
    setDeletingId(post.id);
    try {
      await deletePost(post.id);
      setPosts((current) => current.filter((item) => item.id !== post.id));
      setPendingDeleteId(null);
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.message : "Could not delete the post");
    } finally {
      setDeletingId(null);
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(refresh, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  return (
    <section>
      <div className="section-heading">
        <div>
          <h1>Posts</h1>
          <p className="lede">Times are shown in your local timezone.</p>
        </div>
        <button className="button secondary" type="button" onClick={refresh} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      {error ? (
        <p className="banner error">
          {error}{" "}
          {error.includes("connected") ? <Link to="/connection">Connect LinkedIn</Link> : null}
        </p>
      ) : null}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Content</th>
              <th>Scheduled Time</th>
              <th>Status</th>
              <th>Attempts</th>
              <th>Created At</th>
              <th>Error</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {posts.length === 0 ? (
              <tr>
                <td colSpan={7} className="empty">
                  {loading ? "Loading posts…" : "No posts yet."}
                </td>
              </tr>
            ) : (
              posts.map((post) => (
                <tr key={post.id}>
                  <td className="content-cell" title={post.content}>
                    {post.has_image ? <span className="image-chip">Image</span> : null}
                    {post.content}
                  </td>
                  <td>{formatTimestamp(post.scheduled_at)}</td>
                  <td>
                    <span className={`badge ${post.status}`}>{post.status.toUpperCase()}</span>
                  </td>
                  <td>{post.attempt_count}</td>
                  <td>{formatTimestamp(post.created_at)}</td>
                  <td>{post.last_error ?? "—"}</td>
                  <td>
                    {pendingDeleteId === post.id ? (
                      <div className="delete-confirm">
                        <button
                          className="button danger"
                          type="button"
                          disabled={deletingId === post.id}
                          onClick={() => confirmDelete(post)}
                        >
                          {deletingId === post.id ? "Deleting…" : "Confirm"}
                        </button>
                        <button
                          className="button secondary"
                          type="button"
                          disabled={deletingId === post.id}
                          onClick={() => setPendingDeleteId(null)}
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button className="button danger" type="button" onClick={() => setPendingDeleteId(post.id)}>
                        Delete
                      </button>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
