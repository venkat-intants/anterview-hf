// useLiveKitInterview — manages a full LiveKit interview room lifecycle.
//
// Responsibilities:
//   1. Fetch a signed LiveKit token from interview_core via getRoomToken().
//   2. Connect the LiveKit Room with that token.
//   3. Enable the candidate's microphone so the backend agent can do STT.
//      Browser does NOT do STT/VAD — just publishing the mic track is enough.
//   4. Subscribe to the "simli-avatar-agent" participant's video + audio tracks.
//      Video is exposed as a ref the caller attaches to a <video> element.
//      Audio autoplays natively through the LiveKit SDK's attach() call.
//   5. Expose clean connect() / disconnect() functions and reactive status/error.
//
// The hook does NOT touch localStorage or manage tokens directly — all auth
// flows through clientFetch in getRoomToken (Bearer + httpOnly cookie path).

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Room,
  RoomEvent,
  ConnectionState,
  RemoteParticipant,
  RemoteTrack,
  RemoteTrackPublication,
  Track,
} from 'livekit-client';
import { getRoomToken } from '../api/rooms';
import { toast } from '../lib/toast';

// In this 1-on-1 interview the only REMOTE participants are agent-side:
//   - voice-only mode: 'interviewer-{room}' publishes the TTS audio track
//   - simli mode:      'simli-avatar-agent' publishes the lip-synced video+audio
// The candidate is the LOCAL participant (their own mic is never delivered back
// to them via TrackSubscribed), so we attach playable tracks from ANY remote
// participant rather than filtering on a single hardcoded identity.

export type LiveKitStatus =
  | 'idle'
  | 'fetching-token'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'disconnected'
  | 'error';

export interface UseLiveKitInterviewReturn {
  /** Current connection status */
  status: LiveKitStatus;
  /** Human-readable error string, non-null when status === 'error' */
  error: string | null;
  /** Whether the candidate's mic is currently enabled */
  isMicEnabled: boolean;
  /** Whether the candidate's camera is currently enabled (Phase A — 1:1 video). */
  isCameraEnabled: boolean;
  /**
   * Ref pointing to the avatar <video> element managed by this hook.
   * Attach this ref to the <video> element in your component — the hook calls
   * track.attach(element) whenever the agent's video track is available and
   * track.detach() on cleanup.
   */
  videoRef: React.RefObject<HTMLVideoElement>;
  /**
   * Ref pointing to the candidate's own self-view <video> element. The hook
   * attaches the local camera track here when the camera is enabled. The
   * candidate's video is published to the room (for proctoring) AND mirrored
   * locally so they can see themselves.
   */
  localVideoRef: React.RefObject<HTMLVideoElement>;
  /** Connect to the room for the given session — safe to call multiple times (idempotent). */
  connect: () => Promise<void>;
  /** Disconnect from the room and clean up all tracks. */
  disconnect: () => Promise<void>;
  /** Toggle the candidate's mic on / off. */
  toggleMic: () => Promise<void>;
  /** Toggle the candidate's camera on / off. */
  toggleCamera: () => Promise<void>;
}

export function useLiveKitInterview(
  sessionId: string,
  enableCamera = false,
): UseLiveKitInterviewReturn {
  const [status, setStatus] = useState<LiveKitStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const [isMicEnabled, setIsMicEnabled] = useState(false);
  const [isCameraEnabled, setIsCameraEnabled] = useState(false);

  // Ref to the avatar <video> element; the hook attaches / detaches the remote track.
  const videoRef = useRef<HTMLVideoElement>(null);
  // Ref to the candidate's self-view <video> element (local camera track).
  const localVideoRef = useRef<HTMLVideoElement>(null);

  // Keep the latest enableCamera flag readable inside the connect() closure
  // without making connect() depend on it (which would tear down the room).
  const enableCameraRef = useRef(enableCamera);
  useEffect(() => {
    enableCameraRef.current = enableCamera;
  }, [enableCamera]);

  // Single Room instance — created once, reused across reconnects.
  const roomRef = useRef<Room | null>(null);

  // Track whether we've started a connection attempt to prevent double-connects.
  const isConnectingRef = useRef(false);

  // Keep sessionId in a ref so callbacks always see the latest value without
  // becoming stale closures.
  const sessionIdRef = useRef(sessionId);
  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  // ---------------------------------------------------------------------------
  // Attach / detach avatar video track to the <video> element
  // ---------------------------------------------------------------------------

  const attachVideoTrack = useCallback((track: RemoteTrack) => {
    if (track.kind !== Track.Kind.Video) return;
    const el = videoRef.current;
    if (el) {
      track.attach(el);
    }
  }, []);

  const detachVideoTrack = useCallback((track: RemoteTrack) => {
    if (track.kind !== Track.Kind.Video) return;
    track.detach();
  }, []);

  // ---------------------------------------------------------------------------
  // Attach the candidate's OWN camera track to the self-view <video> element.
  // Called after setCameraEnabled(true) publishes the local camera track.
  // ---------------------------------------------------------------------------
  const attachLocalCamera = useCallback(() => {
    const room = roomRef.current;
    const el = localVideoRef.current;
    if (!room || !el) return;
    const pub = room.localParticipant.getTrackPublication(Track.Source.Camera);
    const track = pub?.videoTrack ?? pub?.track;
    if (track) track.attach(el);
  }, []);

  // ---------------------------------------------------------------------------
  // Subscribe to existing tracks from the agent (for participants already in
  // the room when we join).
  // ---------------------------------------------------------------------------

  const subscribeAgentTracks = useCallback(
    (participant: RemoteParticipant) => {
      participant.trackPublications.forEach((pub: RemoteTrackPublication) => {
        const track = pub.track;
        if (!track) return;
        if (track.kind === Track.Kind.Video) {
          attachVideoTrack(track);
        } else if (track.kind === Track.Kind.Audio) {
          // Audio autoplays via SDK attach() — the SDK creates the element.
          track.attach();
        }
      });
    },
    [attachVideoTrack],
  );

  // ---------------------------------------------------------------------------
  // Room event handlers
  // ---------------------------------------------------------------------------

  const handleTrackSubscribed = useCallback(
    (track: RemoteTrack) => {
      if (track.kind === Track.Kind.Video) {
        attachVideoTrack(track);
      } else if (track.kind === Track.Kind.Audio) {
        track.attach();
      }
    },
    [attachVideoTrack],
  );

  const handleTrackUnsubscribed = useCallback(
    (track: RemoteTrack) => {
      if (track.kind === Track.Kind.Video) {
        detachVideoTrack(track);
      }
    },
    [detachVideoTrack],
  );

  const handleParticipantConnected = useCallback(
    (participant: RemoteParticipant) => {
      subscribeAgentTracks(participant);
    },
    [subscribeAgentTracks],
  );

  const handleConnectionStateChanged = useCallback((state: ConnectionState) => {
    switch (state) {
      case ConnectionState.Connected:
        setStatus('connected');
        setError(null);
        break;
      case ConnectionState.Connecting:
        setStatus('connecting');
        break;
      case ConnectionState.Reconnecting:
        setStatus('reconnecting');
        break;
      case ConnectionState.Disconnected:
        setStatus('disconnected');
        isConnectingRef.current = false;
        break;
      default:
        break;
    }
  }, []);

  const handleDisconnected = useCallback(() => {
    setStatus('disconnected');
    setIsMicEnabled(false);
    setIsCameraEnabled(false);
    isConnectingRef.current = false;
  }, []);

  // ---------------------------------------------------------------------------
  // connect — fetch token, build Room, wire events, join
  // ---------------------------------------------------------------------------

  const connect = useCallback(async () => {
    if (isConnectingRef.current) return;
    if (roomRef.current?.state === ConnectionState.Connected) return;

    isConnectingRef.current = true;
    setError(null);
    setStatus('fetching-token');

    let tokenData: { url: string; token: string; room_name: string };

    try {
      tokenData = await getRoomToken(sessionIdRef.current);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to get room token';
      setError(msg);
      setStatus('error');
      toast.error(`Interview connection failed: ${msg}`);
      isConnectingRef.current = false;
      return;
    }

    // Build a fresh Room each connect (handles the case where a previous
    // Room was fully disconnected and garbage-collected).
    const room = new Room();
    roomRef.current = room;

    // Wire room-level events.
    room.on(RoomEvent.ConnectionStateChanged, handleConnectionStateChanged);
    room.on(RoomEvent.Disconnected, handleDisconnected);
    room.on(RoomEvent.TrackSubscribed, handleTrackSubscribed);
    room.on(RoomEvent.TrackUnsubscribed, handleTrackUnsubscribed);
    room.on(RoomEvent.ParticipantConnected, handleParticipantConnected);

    setStatus('connecting');

    try {
      await room.connect(tokenData.url, tokenData.token);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to connect to room';
      setError(msg);
      setStatus('error');
      toast.error(`Interview connection failed: ${msg}`);
      isConnectingRef.current = false;
      return;
    }

    // Attach any agent tracks already in the room before we subscribed.
    room.remoteParticipants.forEach((p) => {
      subscribeAgentTracks(p);
    });

    // Connected — clear the in-flight guard so a later reconnect (e.g. the
    // Retry button, or a network drop+recover) can call connect() again.
    // Without this, isConnectingRef stays true forever after the first success.
    isConnectingRef.current = false;

    // Enable the candidate's microphone — backend agent does STT on it.
    // The mic permission prompt appears here (expected, user-gesture gated).
    try {
      await room.localParticipant.setMicrophoneEnabled(true);
      setIsMicEnabled(true);
    } catch {
      // Non-fatal: mic may be denied; the interview can still proceed with
      // audio from the avatar side; surface a warning.
      toast.warning('Microphone access denied — your responses will not be recorded.');
    }

    // Phase A — candidate camera (1:1 video). Only when the candidate has
    // consented (enableCamera). Publishes the camera track to the room AND
    // mirrors it to the self-view element. Non-fatal: a denied/absent camera
    // never blocks the interview — it proceeds audio-only.
    if (enableCameraRef.current) {
      try {
        await room.localParticipant.setCameraEnabled(true);
        attachLocalCamera();
        setIsCameraEnabled(true);
      } catch {
        toast.warning('Camera access denied — continuing without video.');
      }
    }
  }, [
    handleConnectionStateChanged,
    handleDisconnected,
    handleTrackSubscribed,
    handleTrackUnsubscribed,
    handleParticipantConnected,
    subscribeAgentTracks,
    attachLocalCamera,
  ]);

  // ---------------------------------------------------------------------------
  // disconnect — leave room + clean up
  // ---------------------------------------------------------------------------

  const disconnect = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;
    try {
      await room.disconnect();
    } catch {
      // Already disconnected — ignore.
    }
    roomRef.current = null;
    setStatus('disconnected');
    setIsMicEnabled(false);
    setIsCameraEnabled(false);
    isConnectingRef.current = false;
  }, []);

  // ---------------------------------------------------------------------------
  // toggleMic
  // ---------------------------------------------------------------------------

  const toggleMic = useCallback(async () => {
    const room = roomRef.current;
    if (!room || room.state !== ConnectionState.Connected) return;
    const next = !isMicEnabled;
    await room.localParticipant.setMicrophoneEnabled(next);
    setIsMicEnabled(next);
  }, [isMicEnabled]);

  // ---------------------------------------------------------------------------
  // toggleCamera
  // ---------------------------------------------------------------------------

  const toggleCamera = useCallback(async () => {
    const room = roomRef.current;
    if (!room || room.state !== ConnectionState.Connected) return;
    const next = !isCameraEnabled;
    try {
      await room.localParticipant.setCameraEnabled(next);
      if (next) {
        attachLocalCamera();
      } else {
        // Detach the local self-view track when turning the camera off.
        const pub = room.localParticipant.getTrackPublication(Track.Source.Camera);
        (pub?.videoTrack ?? pub?.track)?.detach();
      }
      setIsCameraEnabled(next);
    } catch {
      toast.warning('Could not toggle the camera.');
    }
  }, [isCameraEnabled, attachLocalCamera]);

  // ---------------------------------------------------------------------------
  // Cleanup on unmount
  // ---------------------------------------------------------------------------

  useEffect(() => {
    return () => {
      // C6 fix: reset the in-flight guard so the StrictMode remount (dev runs
      // mount→unmount→mount) can connect again. Without this, the second mount
      // sees isConnectingRef.current === true and silently no-ops forever.
      isConnectingRef.current = false;
      const room = roomRef.current;
      if (room && room.state !== ConnectionState.Disconnected) {
        void room.disconnect();
      }
      roomRef.current = null;
    };
  }, []);

  return {
    status,
    error,
    isMicEnabled,
    isCameraEnabled,
    videoRef,
    localVideoRef,
    connect,
    disconnect,
    toggleMic,
    toggleCamera,
  };
}
