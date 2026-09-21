// PublicTask — the candidate's own job simulation / portfolio task (PH4-D4),
// reached with no login. Mirrors PublicOffer.test.tsx's shape.
//
// The properties worth pinning:
//   • the token comes from the URL #fragment and is stripped with
//     history.replaceState on arrival — sent only as the X-Task-Token
//     header (api/publicTask.ts), never a URL;
//   • CONSENT IS GIVEN AT START, not at submit (security review, PH4-D4):
//     before the candidate starts, only the brief and the consent notice are
//     shown — never the items, never the reference materials — and the
//     "Begin" control stays disabled until the checkbox is ticked;
//   • once started, submitting is a confirmation ("Send this?"), never a
//     second consent checkbox;
//   • after submitting, the candidate sees "Submitted — with the hiring
//     team" and NOTHING else — no score, no reviewer, no note.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ApiError } from '../api/client';
import type { PublicTask as PublicTaskShape } from '../api/publicTask';

const viewTask = vi.fn();
const startTask = vi.fn();
const saveTaskResponse = vi.fn();
const submitTask = vi.fn();
const addTaskArtifact = vi.fn();
const removeTaskArtifact = vi.fn();
const downloadTaskMaterial = vi.fn();
const withdrawTaskConsent = vi.fn();
vi.mock('../api/publicTask', () => ({
  viewTask: (...a: unknown[]) => viewTask(...a) as unknown,
  startTask: (...a: unknown[]) => startTask(...a) as unknown,
  saveTaskResponse: (...a: unknown[]) => saveTaskResponse(...a) as unknown,
  submitTask: (...a: unknown[]) => submitTask(...a) as unknown,
  addTaskArtifact: (...a: unknown[]) => addTaskArtifact(...a) as unknown,
  removeTaskArtifact: (...a: unknown[]) => removeTaskArtifact(...a) as unknown,
  downloadTaskMaterial: (...a: unknown[]) => downloadTaskMaterial(...a) as unknown,
  withdrawTaskConsent: (...a: unknown[]) => withdrawTaskConsent(...a) as unknown,
}));

import PublicTask from '../pages/PublicTask';

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PublicTask />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const ASSIGNED: PublicTaskShape = {
  status: 'assigned',
  kind: 'job_simulation',
  round_title: 'Take-home simulation',
  company: 'Acme Corp',
  brief: 'Design a small feature end to end.',
  items: [
    {
      key: 'design_doc',
      prompt: 'Describe your approach.',
      response_type: 'text',
      required: true,
      max_chars: 2000,
    },
  ],
  min_artifacts: null,
  max_artifacts: null,
  allow_files: true,
  allow_links: true,
  allowed_link_domains: null,
  materials: [
    {
      id: 'mat-1',
      title: 'Reference spec',
      original_name: 'spec.pdf',
      content_type: 'application/pdf',
      size_bytes: 1024,
      position: 0,
    },
  ],
  due_at: '2026-10-10T00:00:00.000Z',
  time_limit_seconds: 1800,
  started_at: null,
  submitted_at: null,
  consent_withdrawn: false,
  adjustments: { extra_time_seconds: null, deadline_extended: false },
  responses: [],
};

const IN_PROGRESS: PublicTaskShape = {
  ...ASSIGNED,
  status: 'in_progress',
  started_at: '2026-09-21T10:00:00.000Z',
};

const SUBMITTED: PublicTaskShape = {
  ...IN_PROGRESS,
  status: 'submitted',
  submitted_at: '2026-09-21T10:20:00.000Z',
};

const replaceState = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  window.location.hash = '#task_tok_123456';
  window.history.replaceState = replaceState;
  viewTask.mockResolvedValue(ASSIGNED);
});

afterEach(() => {
  window.location.hash = '';
});

describe('PublicTask — the link itself', () => {
  it('reads the token from the URL fragment and strips it from the address bar', async () => {
    renderPage();
    await screen.findByText('Take-home simulation');

    expect(viewTask).toHaveBeenCalledWith('task_tok_123456');
    expect(replaceState).toHaveBeenCalledWith(null, '', '/');
  });

  it('never calls the API and shows an invalid link when there is no token', async () => {
    window.location.hash = '';
    renderPage();

    expect(await screen.findByText(/isn't valid/i)).toBeInTheDocument();
    expect(viewTask).not.toHaveBeenCalled();
  });

  it('shows the server sentence verbatim for a bad link', async () => {
    viewTask.mockRejectedValue(new ApiError("This task link isn't available.", 404));
    renderPage();
    expect(await screen.findByText("This task link isn't available.")).toBeInTheDocument();
  });

  it('offers a retry, not "invalid", for a transient failure', async () => {
    viewTask.mockRejectedValue(new ApiError('Bad gateway', 503));
    renderPage();

    expect(
      await screen.findByRole('button', { name: /try again/i }, { timeout: 8000 }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/isn't valid/i)).not.toBeInTheDocument();
  });
});

describe('PublicTask — before starting: consent, nothing else', () => {
  it('shows the brief but never the items or materials before starting', async () => {
    renderPage();
    await screen.findByText('Take-home simulation');

    expect(screen.getByText('Design a small feature end to end.')).toBeInTheDocument();
    expect(screen.queryByText('Describe your approach.')).not.toBeInTheDocument();
    expect(screen.queryByText('Reference spec')).not.toBeInTheDocument();
    expect(viewTask).toHaveBeenCalledTimes(1);
  });

  it('shows the consent notice and keeps Begin disabled until it is ticked', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Take-home simulation');

    expect(screen.getByText(/share your answers/i)).toBeInTheDocument();
    const begin = screen.getByRole('button', { name: /begin/i });
    expect(begin).toBeDisabled();
    expect(startTask).not.toHaveBeenCalled();

    await user.click(screen.getByRole('checkbox'));
    expect(begin).toBeEnabled();
  });

  // Security review, PH4-D4 wave 5: the consent copy previously hedged with
  // "may be outside India" and pointed withdrawal at emailing the hiring
  // team, when nothing could actually withdraw it. Both are now accurate.
  it('names the actual storage locations and says withdrawal happens on this page', async () => {
    renderPage();
    await screen.findByText('Take-home simulation');

    expect(screen.getByText(/Singapore, United States/)).toBeInTheDocument();
    expect(screen.queryByText(/may be outside India/i)).not.toBeInTheDocument();
    expect(screen.getByText(/withdraw this consent at any time on this page/i)).toBeInTheDocument();
    expect(screen.queryByText(/contacting the hiring team/i)).not.toBeInTheDocument();
  });

  it('sends consent on start, and only then shows items and materials', async () => {
    const user = userEvent.setup();
    startTask.mockResolvedValue(IN_PROGRESS);
    renderPage();
    await screen.findByText('Take-home simulation');

    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: /begin/i }));

    await waitFor(() => expect(startTask).toHaveBeenCalledWith('task_tok_123456', true));
    expect(await screen.findByText('Describe your approach.')).toBeInTheDocument();
    expect(screen.getByText('Reference spec')).toBeInTheDocument();
  });

  it('cannot be started twice in a row without a second click', async () => {
    const user = userEvent.setup();
    startTask.mockResolvedValue(IN_PROGRESS);
    renderPage();
    await screen.findByText('Take-home simulation');
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: /begin/i }));
    await waitFor(() => expect(startTask).toHaveBeenCalledTimes(1));
  });
});

describe('PublicTask — working on it', () => {
  beforeEach(() => {
    viewTask.mockResolvedValue(IN_PROGRESS);
  });

  it('autosaves a text answer on blur', async () => {
    const user = userEvent.setup();
    saveTaskResponse.mockResolvedValue({ item_key: 'design_doc', saved: true });
    renderPage();

    const textarea = await screen.findByPlaceholderText(/write your answer/i);
    await user.type(textarea, 'My approach is...');
    await user.tab();

    await waitFor(() =>
      expect(saveTaskResponse).toHaveBeenCalledWith('task_tok_123456', 'design_doc', {
        text_value: 'My approach is...',
      }),
    );
  });

  it('confirms before sending the finished work, without asking for consent again', async () => {
    const user = userEvent.setup();
    submitTask.mockResolvedValue(SUBMITTED);
    viewTask.mockResolvedValue({
      ...IN_PROGRESS,
      responses: [
        {
          id: 'r-1',
          item_key: 'design_doc',
          response_type: 'text',
          text_value: 'Done.',
          link_url: null,
          link_kind: null,
          title: null,
          description: null,
          original_name: null,
          content_type: null,
          size_bytes: null,
        },
      ],
    });
    renderPage();
    await screen.findByText('Take-home simulation');

    // No second consent checkbox anywhere on the working page.
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /^submit$/i }));
    expect(submitTask).not.toHaveBeenCalled();
    expect(await screen.findByText(/send this to the hiring team/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /yes, submit/i }));
    await waitFor(() => expect(submitTask).toHaveBeenCalledWith('task_tok_123456'));
  });
});

// A "file" item is answered through POST /task/artifacts carrying the item's
// key — before that existed, the page could only say the item was
// unanswerable, and a REQUIRED file item made the task impossible to submit.
describe('PublicTask — a file item', () => {
  const WITH_FILE_ITEM: PublicTaskShape = {
    ...IN_PROGRESS,
    items: [
      {
        key: 'plan',
        prompt: 'Upload your plan.',
        response_type: 'file',
        required: true,
        max_chars: null,
      },
    ],
  };
  const UPLOADED = {
    id: 'r-file',
    item_key: 'plan',
    response_type: 'file' as const,
    text_value: null,
    link_url: null,
    link_kind: null,
    title: null,
    description: null,
    original_name: 'plan.pdf',
    content_type: 'application/pdf',
    size_bytes: 2048,
  };

  it('uploads the file against the item, not as a free-form artifact', async () => {
    const user = userEvent.setup();
    viewTask.mockResolvedValue(WITH_FILE_ITEM);
    addTaskArtifact.mockResolvedValue({ id: 'r-file' });
    renderPage();

    const input = await screen.findByLabelText(/upload a file/i);
    const file = new File(['%PDF-1.4'], 'plan.pdf', { type: 'application/pdf' });
    await user.upload(input, file);

    await waitFor(() =>
      expect(addTaskArtifact).toHaveBeenCalledWith('task_tok_123456', {
        kind: 'file',
        file,
        itemKey: 'plan',
      }),
    );
  });

  it('keeps Submit disabled until a required file item has its file', async () => {
    viewTask.mockResolvedValue(WITH_FILE_ITEM);
    renderPage();
    await screen.findByLabelText(/upload a file/i);
    expect(screen.getByRole('button', { name: /^submit$/i })).toBeDisabled();
  });

  it('shows the uploaded file, offers Replace, and lets the candidate remove it', async () => {
    const user = userEvent.setup();
    viewTask.mockResolvedValue({ ...WITH_FILE_ITEM, responses: [UPLOADED] });
    removeTaskArtifact.mockResolvedValue(undefined);
    renderPage();

    expect(await screen.findByText('plan.pdf')).toBeInTheDocument();
    expect(screen.getByLabelText(/replace/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^submit$/i })).toBeEnabled();

    await user.click(screen.getByRole('button', { name: /remove plan\.pdf/i }));
    await waitFor(() =>
      expect(removeTaskArtifact).toHaveBeenCalledWith('task_tok_123456', 'r-file'),
    );
  });

  it('refuses a file type the server would refuse, without uploading it', async () => {
    const user = userEvent.setup({ applyAccept: false });
    viewTask.mockResolvedValue(WITH_FILE_ITEM);
    renderPage();

    const input = await screen.findByLabelText(/upload a file/i);
    await user.upload(input, new File(['hello'], 'notes.txt', { type: 'text/plain' }));

    expect(await screen.findByText(/please upload a pdf, jpeg or png/i)).toBeInTheDocument();
    expect(addTaskArtifact).not.toHaveBeenCalled();
  });
});

// Security review, PH4-D4 wave 5: the consent notice says the candidate can
// withdraw ON THIS PAGE, and `POST /task/consent/withdraw` needs a control
// that actually calls it. Mirrors PublicOffer.test.tsx's document-consent
// withdrawal shape: a plain link, a two-step confirm, then a done state.
describe('PublicTask — withdrawing consent', () => {
  beforeEach(() => {
    viewTask.mockResolvedValue(IN_PROGRESS);
  });

  it('withdraws consent through a two-step confirm, then disables further edits', async () => {
    const user = userEvent.setup();
    withdrawTaskConsent.mockResolvedValue({ withdrawn: true });
    renderPage();
    await screen.findByText('Take-home simulation');

    await user.click(screen.getByRole('button', { name: /withdraw my consent/i }));
    expect(withdrawTaskConsent).not.toHaveBeenCalled();
    expect(await screen.findByText(/won't be able to send any more/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /yes, withdraw consent/i }));
    await waitFor(() => expect(withdrawTaskConsent).toHaveBeenCalledWith('task_tok_123456'));

    expect(
      await screen.findByText(/nothing more can be sent for this task/i),
    ).toBeInTheDocument();
    // The item field is now read-only, and there is no submit control left.
    expect(screen.getByPlaceholderText(/write your answer/i)).toBeDisabled();
    expect(screen.queryByRole('button', { name: /^submit$/i })).not.toBeInTheDocument();
  });

  it('can be cancelled without withdrawing anything', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Take-home simulation');

    await user.click(screen.getByRole('button', { name: /withdraw my consent/i }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(screen.getByRole('button', { name: /withdraw my consent/i })).toBeInTheDocument();
    expect(withdrawTaskConsent).not.toHaveBeenCalled();
  });

  it('shows the server refusal verbatim on a repeat withdrawal', async () => {
    const user = userEvent.setup();
    withdrawTaskConsent.mockRejectedValue(
      new ApiError('Consent for this task is already withdrawn.', 409),
    );
    renderPage();
    await screen.findByText('Take-home simulation');

    await user.click(screen.getByRole('button', { name: /withdraw my consent/i }));
    await user.click(screen.getByRole('button', { name: /yes, withdraw consent/i }));

    expect(
      await screen.findByText('Consent for this task is already withdrawn.'),
    ).toBeInTheDocument();
  });
});

// Security review, PH4-D4 wave 5: `GET /task` now reports whether consent
// was withdrawn, so a reload of an already-withdrawn in-progress task must
// show the withdrawn state immediately rather than losing it (the old
// "local state only" behaviour this file's comments used to describe).
describe('PublicTask — consent already withdrawn (e.g. after a reload)', () => {
  it('shows the withdrawn state on load: no withdraw link, and the field disabled', async () => {
    viewTask.mockResolvedValue({ ...IN_PROGRESS, consent_withdrawn: true });
    renderPage();
    await screen.findByText('Take-home simulation');

    expect(
      screen.queryByRole('button', { name: /withdraw my consent/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText(/write your answer/i)).toBeDisabled();
  });
});

describe('PublicTask — after submitting', () => {
  it('shows "Submitted — with the hiring team" and never an evaluation', async () => {
    viewTask.mockResolvedValue(SUBMITTED);
    renderPage();

    expect(await screen.findByText('Submitted')).toBeInTheDocument();
    expect(screen.getByText(/with the hiring team/i)).toBeInTheDocument();
    expect(screen.queryByText(/score/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/reviewer/i)).not.toBeInTheDocument();
    expect(screen.queryByText('Describe your approach.')).not.toBeInTheDocument();
  });

  // Security review, PH4-D4 wave 5: POST /task/consent/withdraw now succeeds
  // on a `submitted` task too. Same two-step control as the in-progress
  // workspace; success invalidates/refetches so the page reflects the
  // server's own `consent_withdrawn` afterwards.
  it('offers to withdraw consent for already-submitted work, and refetches on success', async () => {
    const user = userEvent.setup();
    viewTask
      .mockResolvedValueOnce({ ...SUBMITTED, consent_withdrawn: false })
      .mockResolvedValueOnce({ ...SUBMITTED, consent_withdrawn: true });
    withdrawTaskConsent.mockResolvedValue({ withdrawn: true });
    renderPage();
    await screen.findByText('Submitted');

    await user.click(screen.getByRole('button', { name: /withdraw my consent/i }));
    expect(withdrawTaskConsent).not.toHaveBeenCalled();
    expect(screen.getByText(/no longer be able to see it/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /yes, withdraw consent/i }));
    await waitFor(() => expect(withdrawTaskConsent).toHaveBeenCalledWith('task_tok_123456'));

    expect(await screen.findByText(/no longer see this submission/i)).toBeInTheDocument();
    expect(viewTask).toHaveBeenCalledTimes(2);
  });

  it('shows a done message, and no withdraw control, once consent is already withdrawn', async () => {
    viewTask.mockResolvedValue({ ...SUBMITTED, consent_withdrawn: true });
    renderPage();
    await screen.findByText('Submitted');

    expect(screen.getByText(/no longer see this submission/i)).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /withdraw my consent/i }),
    ).not.toBeInTheDocument();
  });
});
