import React, { useState, useCallback, useRef, useEffect } from 'react';
import { documentService } from '../services/documentService';
import './DocumentPanel.css';

/**
 * DocumentPanel — slide-in drawer for RAG + file conversion
 *
 * Features:
 *  - Upload PDF for RAG (drag & drop or click)
 *  - Ingest URL for RAG
 *  - List ingested documents with delete
 *  - Active document selector (queries are grounded on selected doc)
 *  - PDF ↔ DOCX conversion with instant download
 */
function DocumentPanel({ isOpen, onClose, activeDocIds = new Set(), onDocSelect }) {
  const [tab,        setTab]        = useState('rag');     // 'rag' | 'convert'
  const [docs,       setDocs]       = useState([]);
  const [urlInput,   setUrlInput]   = useState('');
  const [status,     setStatus]     = useState('');
  const [loading,    setLoading]    = useState(false);
  const [dragOver,   setDragOver]   = useState(false);
  const fileRef = useRef(null);
  const convRef = useRef(null);

  // Load documents when panel opens
  useEffect(() => {
    if (isOpen) refreshDocs();
  }, [isOpen]);

  const refreshDocs = useCallback(async () => {
    try {
      const data = await documentService.listDocuments();
      setDocs(data.documents || []);
    } catch (e) {
      console.error('List docs:', e);
    }
  }, []);

  const showStatus = useCallback((msg, isError = false) => {
    setStatus({ text: msg, error: isError });
    setTimeout(() => setStatus(''), 4000);
  }, []);

  // ── RAG ingestion ──────────────────────────────────────────────────────────

  const handlePdfUpload = useCallback(async (file) => {
    if (!file || !file.name.toLowerCase().endsWith('.pdf')) {
      showStatus('Please select a PDF file.', true);
      return;
    }
    setLoading(true);
    setStatus({ text: `Ingesting ${file.name}…`, error: false });
    try {
      const result = await documentService.ingestPdf(file);
      showStatus(`✓ ${result.filename} — ${result.chunk_count} chunks${result.image_pages ? `, ${result.image_pages} image pages` : ''}`);
      await refreshDocs();
      onDocSelect?.(result.doc_id); // auto-select newly ingested doc
    } catch (e) {
      showStatus(e.message, true);
    } finally {
      setLoading(false);
    }
  }, [refreshDocs, onDocSelect, showStatus]);

  const handleUrlIngest = useCallback(async () => {
    const url = urlInput.trim();
    if (!url) return;
    setLoading(true);
    setStatus({ text: `Scraping ${url.slice(0, 50)}…`, error: false });
    try {
      const result = await documentService.ingestUrl(url);
      showStatus(`✓ URL ingested — ${result.chunk_count} chunks`);
      setUrlInput('');
      await refreshDocs();
      onDocSelect?.(result.doc_id);
    } catch (e) {
      showStatus(e.message, true);
    } finally {
      setLoading(false);
    }
  }, [urlInput, refreshDocs, onDocSelect, showStatus]);

  const handleDeleteDoc = useCallback(async (docId) => {
    try {
      await documentService.deleteDocument(docId);
      if (activeDocIds.has(docId)) onDocSelect?.(docId); // toggle it out of the active set
      await refreshDocs();
      showStatus('Document deleted');
    } catch (e) {
      showStatus(e.message, true);
    }
  }, [activeDocIds, onDocSelect, refreshDocs, showStatus]);

  // ── File conversion ────────────────────────────────────────────────────────

  const handleConvert = useCallback(async (file) => {
    if (!file) return;
    const name = file.name.toLowerCase();
    setLoading(true);
    setStatus({ text: `Converting ${file.name}…`, error: false });
    try {
      if (name.endsWith('.pdf')) {
        await documentService.convertPdfToDocx(file);
        showStatus('✓ Converted to DOCX — downloading…');
      } else if (name.endsWith('.docx') || name.endsWith('.doc')) {
        await documentService.convertDocxToPdf(file);
        showStatus('✓ Converted to PDF — downloading…');
      } else {
        showStatus('Supported formats: .pdf, .docx, .doc', true);
      }
    } catch (e) {
      showStatus(e.message, true);
    } finally {
      setLoading(false);
    }
  }, [showStatus]);

  // ── Drag & drop ────────────────────────────────────────────────────────────

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (tab === 'rag')     handlePdfUpload(file);
    else                   handleConvert(file);
  }, [tab, handlePdfUpload, handleConvert]);

  const formatDate = (ts) => new Date(ts * 1000).toLocaleDateString();

  if (!isOpen) return null;

  return (
    <div className="doc-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="doc-panel">

        {/* Header */}
        <div className="doc-header">
          <span className="doc-title">documents</span>
          <div className="doc-tabs">
            <button className={`doc-tab ${tab==='rag' ? 'active':''}`}     onClick={() => setTab('rag')}>RAG</button>
            <button className={`doc-tab ${tab==='convert' ? 'active':''}`} onClick={() => setTab('convert')}>Convert</button>
          </div>
          <button className="doc-close" onClick={onClose}>×</button>
        </div>

        {/* Status bar */}
        {status && (
          <div className={`doc-status ${status.error ? 'doc-status-error' : ''}`}>
            {status.text}
          </div>
        )}

        {/* ── RAG tab ── */}
        {tab === 'rag' && (
          <div className="doc-body">

            {/* Drop zone */}
            <div
              className={`doc-dropzone ${dragOver ? 'dragover' : ''} ${loading ? 'loading' : ''}`}
              onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              onClick={() => !loading && fileRef.current?.click()}
            >
              <svg width="24" height="24" viewBox="0 0 24 24" fill="rgba(255,255,255,0.3)">
                <path d="M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6zm4 18H6V4h7v5h5v11z"/>
              </svg>
              <span>{loading ? 'processing…' : 'drop PDF here or click to upload'}</span>
              <input
                ref={fileRef}
                type="file"
                accept=".pdf"
                style={{ display: 'none' }}
                onChange={(e) => handlePdfUpload(e.target.files[0])}
              />
            </div>

            {/* URL ingest */}
            <div className="doc-url-row">
              <input
                className="doc-url-input"
                type="text"
                placeholder="or paste a URL to scrape…"
                value={urlInput}
                onChange={(e) => setUrlInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleUrlIngest()}
                disabled={loading}
              />
              <button
                className="doc-url-btn"
                onClick={handleUrlIngest}
                disabled={!urlInput.trim() || loading}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M3.9 12c0-1.71 1.39-3.1 3.1-3.1h4V7H7c-2.76 0-5 2.24-5 5s2.24 5 5 5h4v-1.9H7c-1.71 0-3.1-1.39-3.1-3.1zM8 13h8v-2H8v2zm9-6h-4v1.9h4c1.71 0 3.1 1.39 3.1 3.1s-1.39 3.1-3.1 3.1h-4V17h4c2.76 0 5-2.24 5-5s-2.24-5-5-5z"/>
                </svg>
              </button>
            </div>

            {/* Document list */}
            {docs.length > 0 && (
              <div className="doc-list">
                <div className="doc-list-label">ingested documents</div>
                {docs.map((doc) => {
                  const isActive = activeDocIds.has(doc.doc_id);
                  return (
                    <div
                      key={doc.doc_id}
                      className={`doc-item ${isActive ? 'doc-item-active' : ''}`}
                      onClick={() => onDocSelect?.(doc.doc_id)}
                    >
                      {/* Toggle indicator */}
                      <div className="doc-item-toggle">
                        {isActive
                          ? <svg width="14" height="14" viewBox="0 0 24 24" fill="rgba(255,255,255,0.8)"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>
                          : <div className="doc-item-toggle-empty"/>
                        }
                      </div>
                      <div className="doc-item-icon">
                        {doc.source_type === 'pdf'
                          ? <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6zm4 18H6V4h7v5h5v11z"/></svg>
                          : <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M3.9 12c0-1.71 1.39-3.1 3.1-3.1h4V7H7c-2.76 0-5 2.24-5 5s2.24 5 5 5h4v-1.9H7c-1.71 0-3.1-1.39-3.1-3.1z"/></svg>
                        }
                      </div>
                      <div className="doc-item-info">
                        <div className="doc-item-name">{doc.filename.length > 32 ? doc.filename.slice(0,29)+'…' : doc.filename}</div>
                        <div className="doc-item-meta">{doc.chunk_count} chunks · {formatDate(doc.created_at)}</div>
                      </div>
                      {isActive && <div className="doc-item-active-badge">active</div>}
                      <button
                        className="doc-item-delete"
                        onClick={(e) => { e.stopPropagation(); handleDeleteDoc(doc.doc_id); }}
                        title="Delete"
                      >×</button>
                    </div>
                  );
                })}
                {activeDocIds.size > 0 && (
                  <button className="doc-clear-btn" onClick={() => onDocSelect?.(null)}>
                    deactivate all ({activeDocIds.size})
                  </button>
                )}
              </div>
            )}

            {docs.length === 0 && !loading && (
              <p className="doc-empty">No documents ingested yet.<br/>Upload a PDF or paste a URL above.</p>
            )}
          </div>
        )}

        {/* ── Convert tab ── */}
        {tab === 'convert' && (
          <div className="doc-body">
            <div
              className={`doc-dropzone ${dragOver ? 'dragover' : ''} ${loading ? 'loading' : ''}`}
              onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              onClick={() => !loading && convRef.current?.click()}
            >
              <svg width="24" height="24" viewBox="0 0 24 24" fill="rgba(255,255,255,0.3)">
                <path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-5 14H7v-2h7v2zm3-4H7v-2h10v2zm0-4H7V7h10v2z"/>
              </svg>
              <span>{loading ? 'converting…' : 'drop .pdf or .docx to convert'}</span>
              <div className="doc-convert-hint">pdf → docx &nbsp;·&nbsp; docx → pdf</div>
              <input
                ref={convRef}
                type="file"
                accept=".pdf,.docx,.doc"
                style={{ display: 'none' }}
                onChange={(e) => handleConvert(e.target.files[0])}
              />
            </div>
            <p className="doc-empty" style={{marginTop:'1rem'}}>
              Converted file downloads automatically.<br/>
              DOCX→PDF requires Microsoft Word (Windows) or LibreOffice (Linux/macOS).
            </p>
          </div>
        )}

      </div>
    </div>
  );
}

export default DocumentPanel;
