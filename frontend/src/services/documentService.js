/**
 * Document & Conversion API service
 */

class DocumentService {
  /** Upload a PDF for RAG ingestion */
  async ingestPdf(file) {
    const form = new FormData();
    form.append('file', file, file.name);
    const res = await fetch('/api/v1/docs/ingest-pdf', { method: 'POST', body: form });
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || `Ingest failed: ${res.status}`); }
    return await res.json();
    // { doc_id, filename, chunk_count, image_pages }
  }

  /** Ingest a URL for RAG */
  async ingestUrl(url) {
    const res = await fetch('/api/v1/docs/ingest-url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || `Ingest failed: ${res.status}`); }
    return await res.json();
    // { doc_id, url, chunk_count }
  }

  /** List all ingested documents */
  async listDocuments() {
    const res = await fetch('/api/v1/docs/list');
    if (!res.ok) return { documents: [] };
    return await res.json();
    // { documents: [{ doc_id, filename, source_type, chunk_count, image_pages, created_at }] }
  }

  /** Delete a document */
  async deleteDocument(docId) {
    const res = await fetch(`/api/v1/docs/${docId}`, { method: 'DELETE' });
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || 'Delete failed'); }
    return await res.json();
  }

  // ── File conversion ──────────────────────────────────────────────────────

  /**
   * Convert PDF → DOCX. Triggers browser download.
   * @param {File} file
   */
  async convertPdfToDocx(file) {
    const form = new FormData();
    form.append('file', file, file.name);
    const res = await fetch('/api/v1/convert/pdf-to-docx', { method: 'POST', body: form });
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || 'Conversion failed'); }
    return this._downloadBlob(await res.blob(), res, file.name.replace('.pdf', '.docx'));
  }

  /**
   * Convert DOCX → PDF. Triggers browser download.
   * @param {File} file
   */
  async convertDocxToPdf(file) {
    const form = new FormData();
    form.append('file', file, file.name);
    const res = await fetch('/api/v1/convert/docx-to-pdf', { method: 'POST', body: form });
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || 'Conversion failed'); }
    return this._downloadBlob(await res.blob(), res, file.name.replace(/\.docx?$/, '.pdf'));
  }

  _downloadBlob(blob, res, fallbackName) {
    const disposition = res.headers.get('Content-Disposition') || '';
    const nameMatch   = disposition.match(/filename="([^"]+)"/);
    const filename    = nameMatch ? nameMatch[1] : fallbackName;
    const url         = URL.createObjectURL(blob);
    const a           = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    return { filename };
  }
}

export const documentService = new DocumentService();
