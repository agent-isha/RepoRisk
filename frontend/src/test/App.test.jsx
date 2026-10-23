import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App.jsx";
import { analysisId, callsTo, fixtures, json, mockFetch, notSignedIn, signedIn } from "./mockApi.js";

afterEach(() => {
  window.location.hash = "";
});

async function fillLogin(email = "analyst@example.com", password = "correct horse battery staple") {
  await userEvent.type(await screen.findByLabelText("Email"), email);
  await userEvent.type(screen.getByLabelText("Password"), password);
}

describe("login", () => {
  it("checks the session, then shows the login page when signed out", async () => {
    mockFetch({ "GET /api/auth/me": notSignedIn });
    render(<App />);
    expect(screen.getByRole("status")).toHaveTextContent("Checking your session");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
  });

  it("validates the form before calling the API", async () => {
    const fetch = mockFetch({ "GET /api/auth/me": notSignedIn });
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(screen.getByText("Enter your email address.")).toBeInTheDocument();
    expect(screen.getByText("Enter your password.")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Email")).toHaveFocus();

    await userEvent.type(screen.getByLabelText("Email"), "not-an-email");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByText(/Enter a valid email address/)).toBeInTheDocument();
    expect(callsTo(fetch, "POST", "/api/auth/login")).toHaveLength(0);
  });

  it("shows and hides the password", async () => {
    mockFetch({ "GET /api/auth/me": notSignedIn });
    render(<App />);
    const password = await screen.findByLabelText("Password");
    expect(password).toHaveAttribute("type", "password");
    await userEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(password).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "Hide password" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Hide password" }));
    expect(password).toHaveAttribute("type", "password");
  });

  it("shows a loading state, then an error for wrong credentials", async () => {
    let respond;
    mockFetch({
      "GET /api/auth/me": notSignedIn,
      "POST /api/auth/login": () => new Promise((resolve) => { respond = resolve; }),
    });
    render(<App />);
    await fillLogin();
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByRole("button", { name: "Signing in…" })).toBeDisabled();
    expect(screen.getByLabelText("Email")).toBeDisabled();

    respond(json({ error: { code: "invalid_credentials", message: "Incorrect email or password." } }, 401));
    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect email or password.");
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  it("shows the throttling message from the API", async () => {
    mockFetch({
      "GET /api/auth/me": notSignedIn,
      "POST /api/auth/login": json({ error: { code: "too_many_attempts", message: "Too many failed sign-in attempts. Try again in 15 minute(s)." } }, 429),
    });
    render(<App />);
    await fillLogin();
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many failed sign-in attempts");
  });

  it("signs in and opens the dashboard", async () => {
    let signedInYet = false;
    const fetch = mockFetch({
      ...signedIn(),
      "GET /api/auth/me": () => (signedInYet ? json(fixtures.session) : notSignedIn),
      "POST /api/auth/login": () => { signedInYet = true; return json(fixtures.session); },
    });
    render(<App />);
    await fillLogin("  Analyst@Example.com ");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("No analysis results yet")).toBeInTheDocument();
    expect(screen.getAllByText(fixtures.session.user.email).length).toBeGreaterThan(0);
    const [, options] = callsTo(fetch, "POST", "/api/auth/login")[0];
    expect(JSON.parse(options.body)).toEqual({ email: "Analyst@Example.com", password: "correct horse battery staple" });
  });

  it("shows a retryable error when the API is unreachable", async () => {
    let attempts = 0;
    mockFetch({ "GET /api/auth/me": () => (++attempts === 1 ? new TypeError("Failed to fetch") : notSignedIn) });
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the RepoRisk API");
    expect(screen.getByText(/reporisk.api.main:app/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });
});

describe("protected dashboard", () => {
  it("returns to the login page when the session expires", async () => {
    mockFetch({ ...signedIn(), "GET /api/repositories": notSignedIn });
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Your session has ended");
  });

  it("signs out with the CSRF token and returns to the login page", async () => {
    const fetch = mockFetch({ ...signedIn(), "POST /api/auth/logout": json({ status: "logged_out" }) });
    render(<App />);
    await screen.findByText("No analysis results yet");
    await userEvent.click(screen.getAllByRole("button", { name: "Sign out" })[0]);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText("You have been signed out.")).toBeInTheDocument();
    const [, options] = callsTo(fetch, "POST", "/api/auth/logout")[0];
    expect(options.headers["X-CSRF-Token"]).toBe(fixtures.session.csrf_token);
  });

  it("offers GitHub URL analysis when no repositories are configured on the server", async () => {
    mockFetch({ ...signedIn(), "GET /api/repositories": json([]) });
    window.location.hash = "#/analyses";
    render(<App />);
    expect(await screen.findByLabelText("GitHub repository URL")).toBeInTheDocument();
    expect(screen.queryByRole("radiogroup", { name: "Repository source" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("starts an analysis with the CSRF token, follows progress and shows results", async () => {
    const running = { ...fixtures.analysisStatus, status: "running", stage: "tracing_szz",
      stage_label: "Tracing candidate bug-inducing commits", stage_index: 4, message: "10 of 24 likely bug fixes", finished_at: null };
    let polls = 0;
    const fetch = mockFetch({
      ...signedIn(),
      "POST /api/analyze": json(running, 202),
      [`GET /api/analysis/${analysisId}`]: () => json(++polls < 2 ? running : fixtures.analysisStatus),
      [`GET /api/analysis/${analysisId}/summary`]: json(fixtures.summary),
    });
    window.location.hash = "#/analyses";
    render(<App pollMs={5} />);
    await userEvent.click(await screen.findByRole("button", { name: "Start analysis" }));

    expect(await screen.findByText(/Tracing candidate bug-inducing commits — 10 of 24/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "67");
    expect(await screen.findByText(/completed/, { selector: "p" })).toBeInTheDocument();
    const [, options] = callsTo(fetch, "POST", "/api/analyze")[0];
    expect(options.headers["X-CSRF-Token"]).toBe(fixtures.session.csrf_token);

    await userEvent.click(screen.getByRole("link", { name: "Overview" }));
    const highCard = (await screen.findByText("High-risk files")).parentElement;
    expect(within(highCard).getByText(String(fixtures.summary.risk_counts.high))).toBeInTheDocument();
  });

  it("shows why an analysis failed", async () => {
    const failed = { ...fixtures.analysisStatus, status: "failed", error: "GitCommandError: bad object" };
    mockFetch({ ...signedIn(), "POST /api/analyze": json(failed, 202) });
    window.location.hash = "#/analyses";
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Start analysis" }));
    expect(await screen.findByText(/bad object/)).toBeInTheDocument();
  });

  it("shows API errors when an analysis cannot start", async () => {
    mockFetch({
      ...signedIn(),
      "POST /api/analyze": json({ error: { code: "repository_unavailable", message: "Repository 'demo-repo' cannot be analyzed: not a git repository." } }, 409),
    });
    window.location.hash = "#/analyses";
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Start analysis" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not a git repository");
  });

  it("opens the latest completed analysis and navigates with the sidebar", async () => {
    mockFetch({
      ...signedIn([fixtures.analysisStatus]),
      [`GET /api/analysis/${analysisId}/summary`]: json(fixtures.summary),
      [`GET /api/analysis/${analysisId}/files`]: json(fixtures.files),
    });
    render(<App />);
    expect(await screen.findByText("Heuristic risk distribution")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("link", { name: "File risk explorer" })[0]);
    expect(await screen.findByRole("button", { name: "src/core.py" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "File risk explorer" })[0]).toHaveAttribute("aria-current", "page");
    await waitFor(() => expect(window.location.hash).toBe("#/files"));
  });
});

describe("GitHub repository URL", () => {
  const github = { ...fixtures.analysisStatus, repository_id: "github:psf/requests", repository_name: "psf/requests" };

  async function openGithubForm() {
    window.location.hash = "#/analyses";
    render(<App pollMs={5} />);
    await userEvent.click(await screen.findByRole("radio", { name: "GitHub URL" }));
    return screen.getByLabelText("GitHub repository URL");
  }

  it("validates the URL before calling the API", async () => {
    const fetch = mockFetch(signedIn());
    const input = await openGithubForm();
    for (const [value, message] of [
      ["", "Enter a GitHub repository URL"],
      ["http://github.com/psf/requests", "Only https:// GitHub URLs are supported."],
      ["https://gitlab.com/psf/requests", "Use a public repository's main URL"],
      ["https://github.com/psf/requests/tree/main", "Use a public repository's main URL"],
      ["/Users/me/repo", "Only https:// GitHub URLs are supported."],
    ]) {
      await userEvent.clear(input);
      if (value) await userEvent.type(input, value);
      await userEvent.click(screen.getByRole("button", { name: "Start analysis" }));
      expect(screen.getByText(new RegExp(message.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")))).toBeInTheDocument();
      expect(input).toHaveAttribute("aria-invalid", "true");
    }
    expect(callsTo(fetch, "POST", "/api/analyze")).toHaveLength(0);
  });

  it("sends the URL with the CSRF token, shows cloning progress, then results", async () => {
    const cloning = { ...github, status: "running", stage: "cloning", stage_label: "Cloning the repository from GitHub",
      stage_index: 0, message: "Cloning psf/requests from GitHub", finished_at: null };
    let polls = 0;
    const fetch = mockFetch({
      ...signedIn(),
      "POST /api/analyze": json(cloning, 202),
      [`GET /api/analysis/${analysisId}`]: () => json(++polls < 3 ? cloning : github),
      [`GET /api/analysis/${analysisId}/summary`]: json(fixtures.summary),
    });
    const input = await openGithubForm();
    await userEvent.type(input, " https://github.com/psf/requests ");
    await userEvent.click(screen.getByRole("button", { name: "Start analysis" }));

    expect(await screen.findByText(/Cloning the repository from GitHub — Cloning psf\/requests/)).toBeInTheDocument();
    expect(await screen.findByText(/completed/, { selector: "p" })).toBeInTheDocument();
    const [, options] = callsTo(fetch, "POST", "/api/analyze")[0];
    expect(JSON.parse(options.body)).toEqual({ github_url: "https://github.com/psf/requests", options: expect.any(Object) });
    expect(options.headers["X-CSRF-Token"]).toBe(fixtures.session.csrf_token);
  });

  it("shows clone failures", async () => {
    const failed = { ...github, status: "failed", stage: "cloning", error: "psf/requests was not found on GitHub, or it is not public." };
    mockFetch({ ...signedIn(), "POST /api/analyze": json(failed, 202) });
    const input = await openGithubForm();
    await userEvent.type(input, "https://github.com/psf/requests");
    await userEvent.click(screen.getByRole("button", { name: "Start analysis" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not clone psf/requests");
    expect(alert).toHaveTextContent("not found on GitHub, or it is not public");
  });

  it("shows analysis failures after a successful clone", async () => {
    const failed = { ...github, status: "failed", stage: "building_dataset", error: "RuntimeError: dataset failed" };
    mockFetch({ ...signedIn(), "POST /api/analyze": json(failed, 202) });
    const input = await openGithubForm();
    await userEvent.type(input, "https://github.com/psf/requests");
    await userEvent.click(screen.getByRole("button", { name: "Start analysis" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Analysis of psf/requests failed");
  });

  it("shows server-side validation and capacity errors", async () => {
    let call = 0;
    mockFetch({
      ...signedIn(),
      "POST /api/analyze": () => (++call === 1
        ? json({ error: { code: "validation_error", message: "The request is invalid.",
            details: [{ location: "body.github_url", message: "Only repositories on github.com are supported." }] } }, 422)
        : json({ error: { code: "too_many_analyses", message: "3 analyses are already queued or running. Try again when one finishes." } }, 429)),
    });
    const input = await openGithubForm();
    await userEvent.type(input, "https://github.com/psf/requests");
    await userEvent.click(screen.getByRole("button", { name: "Start analysis" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Only repositories on github.com are supported.");
    await userEvent.click(screen.getByRole("button", { name: "Start analysis" }));
    expect(await screen.findByText(/already queued or running/)).toBeInTheDocument();
  });
});

describe("demo credentials", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("shows the demo account below the Sign in button on the dev server", async () => {
    mockFetch({ "GET /api/auth/me": notSignedIn });
    render(<App />);
    const section = await screen.findByRole("region", { name: "Demo Credentials" });
    expect(section).toHaveTextContent("demo@reporisk.local");
    expect(section).toHaveTextContent("RepoRiskDemo2026!");
    const signIn = screen.getByRole("button", { name: "Sign in" });
    expect(signIn.compareDocumentPosition(section) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("copies each value to the clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    mockFetch({ "GET /api/auth/me": notSignedIn });
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Copy demo email" }));
    expect(writeText).toHaveBeenCalledWith("demo@reporisk.local");
    expect(screen.getByRole("button", { name: "Copy demo email" })).toHaveTextContent("Copied");
    await userEvent.click(screen.getByRole("button", { name: "Copy demo password" }));
    expect(writeText).toHaveBeenLastCalledWith("RepoRiskDemo2026!");
  });

  it("reports when the clipboard is unavailable", async () => {
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
    mockFetch({ "GET /api/auth/me": notSignedIn });
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Copy demo password" }));
    expect(await screen.findByText("Copy failed")).toBeInTheDocument();
  });

  it("is hidden outside the dev server unless explicitly enabled", async () => {
    vi.stubEnv("DEV", false);
    vi.stubEnv("VITE_SHOW_DEMO_CREDENTIALS", "");
    mockFetch({ "GET /api/auth/me": notSignedIn });
    const { unmount } = render(<App />);
    await screen.findByRole("heading", { name: "Sign in" });
    expect(screen.queryByText("Demo Credentials")).not.toBeInTheDocument();
    expect(screen.queryByText("RepoRiskDemo2026!")).not.toBeInTheDocument();
    unmount();

    vi.stubEnv("VITE_SHOW_DEMO_CREDENTIALS", "true");
    render(<App />);
    expect(await screen.findByRole("region", { name: "Demo Credentials" })).toBeInTheDocument();
  });
});
