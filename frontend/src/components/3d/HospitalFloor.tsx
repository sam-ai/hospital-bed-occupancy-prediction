"use client";

import React, { Suspense, useRef, useMemo, useState, useEffect, useCallback } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls, Text, Grid, Html, Billboard } from "@react-three/drei";
import { EffectComposer, Bloom } from "@react-three/postprocessing";
import * as THREE from "three";
import { BedState, Patient3D, Staff3D, Position3D, FastTrackMatch } from "@/types/hospital";

/* ── Bed type map (mirrors backend triage layout) ── */
export const FLOOR_BED_TYPES: Record<string, string> = {
  "BED-1": "ICU", "BED-2": "ICU",
  "BED-3": "MED_SURG", "BED-4": "MED_SURG", "BED-5": "MED_SURG", "BED-6": "MED_SURG",
  "BED-7": "TELEMETRY", "BED-8": "TELEMETRY",
  "BED-9": "STEP_DOWN",
  "BED-10": "ISOLATION",
};

export interface PlaybackInfo {
  active: boolean;
  mode: "STEP" | "TIMELINE" | null;
  horizonType: string | null;
  stepIndex: number | null;
  totalSteps: number | null;
  occupiedBeds: number | null;
}

interface HospitalFloorProps {
  beds: BedState[];
  patients: Patient3D[];
  staff: Staff3D[];
  theme: "dark" | "light";
  /** Live forecast playback info for the step-clock HUD. */
  playbackInfo?: PlaybackInfo | null;
  /** Latest event description for the in-scene ticker. */
  lastEvent?: string | null;
  /** Fast-track triage matches (for acuity badges in tooltips). */
  fastTrackMatches?: FastTrackMatch[];
  /** External request to focus the camera on a specific bed (from UI tables). */
  focusBedId?: { bedId: string; token: number } | null;
}

/* ═══════════════════════════════════════════════════════════════
   Theme color palette
   ═══════════════════════════════════════════════════════════════ */
const THEME = {
  dark: {
    clear: "#0b1118",
    floor: "#1a2533",
    floorReflect: "#1e293b",
    gridSection: "#2d3f54",
    gridCell: "#1a2533",
    wall: "#1e293b",
    desk: "#1e293b",
    deskLeg: "#64748b",
    monitorFrame: "#0f172a",
    label: "#94a3b8",
    corridor: "#162030",
    ambientIntensity: 0.35,
    ambientColor: "#bfdbfe",
    sunIntensity: 1.6,
    ceilingLight: "#fef3c7",
    ceilingFixture: "#e2e8f0",
    dust: "#8ab4f8",
    fogNear: 25,
    fogFar: 55,
    plantPot: "#78350f",
    plantLeaf: "#166534",
    ivStand: "#94a3b8",
    curtainRail: "#64748b",
    curtain: "rgba(200,220,240,0.35)",
  },
  light: {
    clear: "#e2e8f0",
    floor: "#d4dbe5",
    floorReflect: "#c8d0dc",
    gridSection: "#b0bac8",
    gridCell: "#d4dbe5",
    wall: "#c8d0dc",
    desk: "#94a3b8",
    deskLeg: "#64748b",
    monitorFrame: "#334155",
    label: "#475569",
    corridor: "#bec7d1",
    ambientIntensity: 0.65,
    ambientColor: "#ffffff",
    sunIntensity: 1.2,
    ceilingLight: "#fef9c3",
    ceilingFixture: "#e2e8f0",
    dust: "#94a3b8",
    fogNear: 30,
    fogFar: 65,
    plantPot: "#92400e",
    plantLeaf: "#15803d",
    ivStand: "#475569",
    curtainRail: "#94a3b8",
    curtain: "rgba(180,200,220,0.3)",
  },
} as const;

/* ── Floor layout constants ── */
const FLOOR_W = 34;
const FLOOR_H = 24;

const PLANT_POSITIONS: [number, number, number][] = [
  [-16, 0, -8], [16, 0, -8],
  [-16, 0, 2], [16, 0, 2],
  [-16, 0, 10], [16, 0, 10],
  [-8, 0, -10], [8, 0, -10],
  [0, 0, -10],
];

/* ═══════════════════════════════════════════════════════════════
   Utility: LERP position with speed
   ═══════════════════════════════════════════════════════════════ */
function lerpTo(
  current: { x: number; y: number; z: number },
  target: { x: number; y: number; z: number },
  speed: number
): { x: number; y: number; z: number } {
  const dx = target.x - current.x;
  const dy = target.y - current.y;
  const dz = target.z - current.z;
  const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
  if (dist < 0.05) return { ...target };
  const step = Math.min(speed, dist);
  return {
    x: current.x + (dx / dist) * step,
    y: current.y + (dy / dist) * step,
    z: current.z + (dz / dist) * step,
  };
}

/* ═══════════════════════════════════════════════════════════════
   Hover / tooltip system
   ═══════════════════════════════════════════════════════════════ */
function useHoverable(): [boolean, (e: { stopPropagation: () => void }) => void, () => void] {
  const [hovered, setHovered] = useState(false);
  const over = useCallback((e: { stopPropagation: () => void }) => {
    e.stopPropagation();
    setHovered(true);
    document.body.style.cursor = "pointer";
  }, []);
  const out = useCallback(() => {
    setHovered(false);
    document.body.style.cursor = "auto";
  }, []);
  useEffect(() => () => { document.body.style.cursor = "auto"; }, []);
  return [hovered, over, out];
}

function TooltipCard({ position, children }: { position: [number, number, number]; children: React.ReactNode }) {
  return (
    <Html position={position} center distanceFactor={14} zIndexRange={[60, 0]} style={{ pointerEvents: "none", userSelect: "none" }}>
      <div
        style={{
          background: "rgba(11,17,24,0.92)",
          border: "1px solid rgba(56,189,248,0.45)",
          borderRadius: 8,
          padding: "7px 10px",
          color: "#f0f6ff",
          fontFamily: "var(--font-sans), sans-serif",
          fontSize: 12,
          lineHeight: 1.55,
          whiteSpace: "nowrap",
          boxShadow: "0 4px 18px rgba(0,0,0,0.5)",
        }}
      >
        {children}
      </div>
    </Html>
  );
}

/** Camera-facing status chip above an entity. */
function StatusChip({
  position,
  text,
  color,
  icon,
}: {
  position: [number, number, number];
  text: string;
  color: string;
  icon?: string;
}) {
  return (
    <Billboard position={position}>
      <Text fontSize={0.16} color={color} anchorX="center" anchorY="bottom" outlineWidth={0.008} outlineColor="#0b1118">
        {icon ? `${icon} ${text}` : text}
      </Text>
    </Billboard>
  );
}

/* ═══════════════════════════════════════════════════════════════
   Dust Particles
   ═══════════════════════════════════════════════════════════════ */
function DustParticles({ color }: { color: string }) {
  const count = 100;
  const mesh = useRef<THREE.InstancedMesh>(null);
  const particles = useMemo(() =>
    Array.from({ length: count }, () => ({
      pos: [(Math.random() - 0.5) * FLOOR_W, Math.random() * 6 + 0.5, (Math.random() - 0.5) * FLOOR_H] as [number, number, number],
      speed: Math.random() * 0.22 + 0.06,
      offset: Math.random() * Math.PI * 2,
    })), []);
  const dummy = useMemo(() => new THREE.Object3D(), []);

  useFrame(({ clock }) => {
    if (!mesh.current) return;
    const t = clock.getElapsedTime();
    particles.forEach((p, i) => {
      dummy.position.set(
        p.pos[0] + Math.sin(t * p.speed + p.offset) * 0.4,
        p.pos[1] + Math.sin(t * p.speed * 0.4 + p.offset) * 0.2,
        p.pos[2] + Math.cos(t * p.speed + p.offset) * 0.4
      );
      dummy.scale.setScalar(0.016);
      dummy.updateMatrix();
      mesh.current!.setMatrixAt(i, dummy.matrix);
    });
    mesh.current.instanceMatrix.needsUpdate = true;
  });

  return (
    <instancedMesh ref={mesh} args={[undefined, undefined, count]}>
      <sphereGeometry args={[1, 6, 6]} />
      <meshBasicMaterial color={color} transparent opacity={0.22} />
    </instancedMesh>
  );
}

/* ═══════════════════════════════════════════════════════════════
   Plant (potted)
   ═══════════════════════════════════════════════════════════════ */
function Plant({ position, potColor, leafColor }: { position: [number, number, number]; potColor: string; leafColor: string }) {
  const leafRef = useRef<THREE.Group>(null);
  useFrame(({ clock }) => {
    if (!leafRef.current) return;
    leafRef.current.rotation.z = Math.sin(clock.getElapsedTime() * 0.8 + position[0]) * 0.03;
  });
  return (
    <group position={position}>
      {/* Pot */}
      <mesh position={[0, 0.2, 0]} castShadow>
        <cylinderGeometry args={[0.22, 0.18, 0.4, 12]} />
        <meshStandardMaterial color={potColor} roughness={0.75} metalness={0.05} />
      </mesh>
      {/* Soil */}
      <mesh position={[0, 0.41, 0]}>
        <cylinderGeometry args={[0.2, 0.2, 0.03, 12]} />
        <meshStandardMaterial color="#451a03" roughness={0.95} />
      </mesh>
      {/* Foliage group */}
      <group ref={leafRef}>
        {/* Main stem */}
        <mesh position={[0, 0.65, 0]} castShadow>
          <cylinderGeometry args={[0.02, 0.025, 0.5, 6]} />
          <meshStandardMaterial color="#166534" roughness={0.8} />
        </mesh>
        {/* Leaves cluster */}
        {[0, 1.2, 2.4, 3.6, 4.8].map((angle, i) => (
          <mesh key={i} position={[Math.cos(angle) * 0.2, 0.7 + i * 0.06, Math.sin(angle) * 0.2]} castShadow>
            <sphereGeometry args={[0.14 + Math.random() * 0.04, 8, 8]} />
            <meshStandardMaterial color={leafColor} roughness={0.85} />
          </mesh>
        ))}
      </group>
    </group>
  );
}

/* ═══════════════════════════════════════════════════════════════
   Occupancy Glow
   ═══════════════════════════════════════════════════════════════ */
function OccupancyGlow({ occupied, cleaning }: { occupied: boolean; cleaning?: boolean }) {
  const ref = useRef<THREE.Mesh>(null);
  useFrame(({ clock }) => {
    if (!ref.current || (!occupied && !cleaning)) return;
    const s = 1 + Math.sin(clock.getElapsedTime() * 2.5) * 0.07;
    ref.current.scale.set(s, 1, s);
  });
  if (!occupied && !cleaning) return null;
  return (
    <mesh ref={ref} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.005, 0]}>
      <ringGeometry args={[0.8, 1.1, 32]} />
      <meshBasicMaterial
        color={cleaning ? "#f59e0b" : "#ef4444"}
        transparent
        opacity={cleaning ? 0.3 : 0.18}
        side={THREE.DoubleSide}
      />
    </mesh>
  );
}

/* ═══════════════════════════════════════════════════════════════
   IV Stand
   ═══════════════════════════════════════════════════════════════ */
function IVStand({ position, color }: { position: [number, number, number]; color: string }) {
  return (
    <group position={position}>
      {/* Pole */}
      <mesh position={[0, 0.6, 0]} castShadow>
        <cylinderGeometry args={[0.015, 0.015, 1.2, 6]} />
        <meshStandardMaterial color={color} metalness={0.85} roughness={0.18} />
      </mesh>
      {/* Base legs */}
      {[0, 2.1, 4.2].map((angle, i) => (
        <mesh key={i} position={[Math.cos(angle) * 0.12, 0.02, Math.sin(angle) * 0.12]} castShadow>
          <boxGeometry args={[0.25, 0.02, 0.03]} />
          <meshStandardMaterial color={color} metalness={0.8} roughness={0.2} />
        </mesh>
      ))}
      {/* Hook */}
      <mesh position={[0, 1.22, 0]}>
        <sphereGeometry args={[0.03, 6, 6]} />
        <meshStandardMaterial color={color} metalness={0.8} />
      </mesh>
      {/* IV bag */}
      <mesh position={[0.04, 1.12, 0]} castShadow>
        <boxGeometry args={[0.08, 0.12, 0.04]} />
        <meshStandardMaterial color="#dbeafe" roughness={0.4} transparent opacity={0.7} />
      </mesh>
    </group>
  );
}

/* ═══════════════════════════════════════════════════════════════
   Hospital Bed — detailed PBR
   ═══════════════════════════════════════════════════════════════ */
function BedMesh({
  bed,
  labelColor,
  ivColor,
  onSelect,
  fastTrackMatches = [],
}: {
  bed: BedState;
  labelColor: string;
  ivColor: string;
  onSelect?: (entity: { kind: "bed"; id: string }) => void;
  fastTrackMatches?: FastTrackMatch[];
}) {
  const [hovered, over, out] = useHoverable();
  const color = bed.isOccupied ? "#dc2626" : bed.isBeingCleaned ? "#d97706" : "#16a34a";
  const frame = "#94a3b8";
  const mattress = bed.isOccupied ? "#fca5a5" : "#bbf7d0";

  const bedType = FLOOR_BED_TYPES[bed.id] ?? "MED_SURG";
  const incomingMatch = fastTrackMatches.find((m) => m.matched_bed_id === bed.id);
  const statusText = bed.isBeingCleaned
    ? "🧽 EVS CLEANING"
    : bed.isOccupied
      ? `OCCUPIED · ${bed.patientId ?? ""}`
      : incomingMatch
        ? `RESERVED · ESI ${incomingMatch.esi_level}`
        : "FREE";

  return (
    <group
      position={[bed.position.x, bed.position.y, bed.position.z]}
      onPointerOver={over}
      onPointerOut={out}
      onClick={(e) => { e.stopPropagation(); onSelect?.({ kind: "bed", id: bed.id }); }}
    >
      <OccupancyGlow occupied={bed.isOccupied} cleaning={bed.isBeingCleaned} />

      {/* Metal legs */}
      {[[-0.8, 0, -0.5], [0.8, 0, -0.5], [-0.8, 0, 0.5], [0.8, 0, 0.5]].map((p, i) => (
        <mesh key={i} position={[p[0], 0.15, p[2]]} castShadow>
          <cylinderGeometry args={[0.035, 0.035, 0.3, 8]} />
          <meshStandardMaterial color={frame} metalness={0.85} roughness={0.18} />
        </mesh>
      ))}

      {/* Frame */}
      <mesh position={[0, 0.28, 0]} castShadow>
        <boxGeometry args={[1.7, 0.05, 1.1]} />
        <meshStandardMaterial color={frame} metalness={0.75} roughness={0.22} />
      </mesh>

      {/* Mattress */}
      <mesh position={[0, 0.38, 0]} castShadow>
        <boxGeometry args={[1.6, 0.16, 1.0]} />
        <meshStandardMaterial color={mattress} roughness={0.88} />
      </mesh>

      {/* Pillow */}
      <mesh position={[-0.6, 0.51, 0]} castShadow>
        <boxGeometry args={[0.32, 0.09, 0.55]} />
        <meshStandardMaterial color="#f1f5f9" roughness={0.92} />
      </mesh>

      {/* Blanket */}
      <mesh position={[0.2, 0.49, 0]} castShadow>
        <boxGeometry args={[0.9, 0.06, 0.95]} />
        <meshStandardMaterial color={bed.isOccupied ? "#fecaca" : "#d1fae5"} roughness={0.9} />
      </mesh>

      {/* Headboard */}
      <mesh position={[-0.85, 0.55, 0]} castShadow>
        <boxGeometry args={[0.06, 0.75, 1.0]} />
        <meshStandardMaterial color={frame} metalness={0.65} roughness={0.28} />
      </mesh>

      {/* Side table */}
      <mesh position={[-1.1, 0.3, 0]} castShadow>
        <boxGeometry args={[0.35, 0.55, 0.35]} />
        <meshStandardMaterial color="#475569" roughness={0.5} metalness={0.2} />
      </mesh>
      <mesh position={[-1.1, 0.59, 0]} castShadow>
        <boxGeometry args={[0.38, 0.02, 0.38]} />
        <meshStandardMaterial color="#64748b" roughness={0.3} metalness={0.4} />
      </mesh>

      {/* IV Stand (only for occupied beds) */}
      {bed.isOccupied && (
        <IVStand position={[1.2, 0, -0.3]} color={ivColor} />
      )}

      <StatusChip
        position={[0, 1.15, 0]}
        text={bed.id.replace("BED-", "B")}
        color={hovered ? "#ffffff" : labelColor}
      />
      {bed.isOccupied && (
        <StatusChip position={[0, 1.42, 0]} text={bed.patientId ?? ""} color="#fbbf24" icon="🛏" />
      )}
      {bed.isBeingCleaned && <StatusChip position={[0, 1.7, 0]} text="EVS CLEANING" color="#f59e0b" icon="🧽" />}
      {incomingMatch && !bed.isOccupied && (
        <StatusChip position={[0, 1.7, 0]} text={`RESERVED ESI ${incomingMatch.esi_level}`} color="#38bdf8" icon="➜" />
      )}

      {hovered && (
        <TooltipCard position={[0, 2.1, 0]}>
          <div style={{ fontWeight: 700, marginBottom: 2 }}>
            {bed.id} · {bedType}
          </div>
          <div>Status: {statusText}</div>
          {incomingMatch && (
            <div>
              Incoming: {incomingMatch.mrn} (ESI {incomingMatch.esi_level}, score{" "}
              {incomingMatch.priority_score})
            </div>
          )}
          {incomingMatch && <div style={{ color: "#94a3b8" }}>{incomingMatch.action_item}</div>}
        </TooltipCard>
      )}
    </group>
  );
}

/* ═══════════════════════════════════════════════════════════════
   Patient Avatar — walks to target, bobs when idle
   ═══════════════════════════════════════════════════════════════ */
function PatientAvatar({
  patient,
  onSelect,
}: {
  patient: Patient3D;
  onSelect?: (entity: { kind: "patient"; id: string; bedId?: string }) => void;
}) {
  const color =
    patient.status === "ARRIVED" ? "#3b82f6" :
    patient.status === "BED_ASSIGNED" ? "#8b5cf6" :
    patient.status === "WALKING" || patient.status === "ESCORTED" || patient.status === "DISCHARGED" ? "#f59e0b" : "#6b7280";

  const [hovered, over, out] = useHoverable();
  const groupRef = useRef<THREE.Group>(null);
  const ringRef = useRef<THREE.Mesh>(null);
  const posRef = useRef({ x: patient.position.x, y: 0.75, z: patient.position.z });

  /* ── Walking trail: fading line of recent positions ── */
  const TRAIL_LEN = 24;
  const trailObj = useMemo(() => {
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(new Float32Array(TRAIL_LEN * 3), 3));
    const mat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.35 });
    return new THREE.Line(geom, mat);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    (trailObj.material as THREE.LineBasicMaterial).color.set(color);
  }, [color, trailObj]);
  useEffect(() => () => { trailObj.geometry.dispose(); (trailObj.material as THREE.Material).dispose(); }, [trailObj]);

  useFrame((_, delta) => {
    if (!groupRef.current || !patient.targetPosition) return;
    const target = { x: patient.targetPosition.x, y: 0.75, z: patient.targetPosition.z };
    posRef.current = lerpTo(posRef.current, target, delta * 2.0);
    groupRef.current.position.set(posRef.current.x, posRef.current.y, posRef.current.z);

    // Update trail buffer
    const attr = trailObj.geometry.getAttribute("position") as THREE.BufferAttribute;
    const arr = attr.array as Float32Array;
    for (let i = arr.length - 3; i >= 3; i -= 3) {
      arr[i] = arr[i - 3]; arr[i + 1] = arr[i - 2]; arr[i + 2] = arr[i - 1];
    }
    arr[0] = posRef.current.x; arr[1] = 0.06; arr[2] = posRef.current.z;
    attr.needsUpdate = true;

    // Walking bob
    const dist = Math.sqrt(
      (target.x - posRef.current.x) ** 2 + (target.z - posRef.current.z) ** 2
    );
    if (dist > 0.1) {
      groupRef.current.position.y += Math.sin(Date.now() * 0.01) * 0.03;
    }

    if (ringRef.current) ringRef.current.rotation.z += delta * 0.5;
  });

  const statusVerb =
    patient.status === "ARRIVED" ? "Waiting in queue" :
    patient.status === "ESCORTED" ? `Escorted → ${patient.bedId ?? "bed"}` :
    patient.status === "BED_ASSIGNED" ? `In ${patient.bedId ?? "bed"}` :
    patient.status === "DISCHARGED" ? "Walking to discharge" :
    patient.status === "WALKING" ? `Walking → ${patient.bedId ?? "…"}` : "Idle";

  return (
    <>
      <primitive object={trailObj} />
      <group
        ref={groupRef}
        position={[patient.position.x, 0.75, patient.position.z]}
        onPointerOver={over}
        onPointerOut={out}
        onClick={(e) => { e.stopPropagation(); onSelect?.({ kind: "patient", id: patient.id, bedId: patient.bedId }); }}
      >
        <mesh castShadow>
          <capsuleGeometry args={[0.2, 0.5, 8, 16]} />
          <meshStandardMaterial
            color={color}
            roughness={0.35}
            metalness={0.1}
            emissive={color}
            emissiveIntensity={hovered ? 0.6 : 0.15}
          />
        </mesh>
        <mesh position={[0, 0.52, 0]} castShadow>
          <sphereGeometry args={[0.16, 16, 16]} />
          <meshStandardMaterial color="#fde68a" roughness={0.55} />
        </mesh>
        <mesh ref={ringRef} position={[0, -0.1, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[0.25, 0.28, 24]} />
          <meshBasicMaterial color={color} transparent opacity={hovered ? 0.7 : 0.35} side={THREE.DoubleSide} />
        </mesh>

        <StatusChip position={[0, 0.95, 0]} text={patient.id.replace("PAT-", "P")} color={hovered ? "#ffffff" : color} />

        {hovered && (
          <TooltipCard position={[0, 1.5, 0]}>
            <div style={{ fontWeight: 700, marginBottom: 2 }}>{patient.id}</div>
            <div>Status: {statusVerb}</div>
          </TooltipCard>
        )}
      </group>
    </>
  );
}

/* ═══════════════════════════════════════════════════════════════
   Staff Avatar — patrols between waypoints
   ═══════════════════════════════════════════════════════════════ */
function StaffAvatar({
  staff,
  onSelect,
}: {
  staff: Staff3D;
  onSelect?: (entity: { kind: "staff"; id: string }) => void;
}) {
  const isDoctor = staff.role === "doctor";
  const baseColor = isDoctor ? "#10b981" : "#22a8cc";
  const color = staff.status === "DISPATCHED" ? "#f59e0b" : baseColor;
  const [hovered, over, out] = useHoverable();

  const groupRef = useRef<THREE.Group>(null);
  const posRef = useRef({ x: staff.position.x, y: 0.78, z: staff.position.z });

  useFrame((_, delta) => {
    if (!groupRef.current || !staff.targetPosition) return;
    const target = { x: staff.targetPosition.x, y: 0.78, z: staff.targetPosition.z };
    posRef.current = lerpTo(posRef.current, target, delta * 1.8);
    groupRef.current.position.set(posRef.current.x, posRef.current.y, posRef.current.z);

    // Walking bob
    const dist = Math.sqrt(
      (target.x - posRef.current.x) ** 2 + (target.z - posRef.current.z) ** 2
    );
    if (dist > 0.1) {
      groupRef.current.position.y += Math.sin(Date.now() * 0.012) * 0.025;
    }
  });

  return (
    <group
      ref={groupRef}
      position={[staff.position.x, 0.78, staff.position.z]}
      onPointerOver={over}
      onPointerOut={out}
      onClick={(e) => { e.stopPropagation(); onSelect?.({ kind: "staff", id: staff.id }); }}
    >
      {/* Body */}
      <mesh castShadow>
        <capsuleGeometry args={[0.22, 0.6, 8, 16]} />
        <meshStandardMaterial color={color} roughness={0.3} metalness={0.12} emissive={color} emissiveIntensity={0.18} />
      </mesh>
      {/* Head */}
      <mesh position={[0, 0.58, 0]} castShadow>
        <sphereGeometry args={[0.18, 16, 16]} />
        <meshStandardMaterial color="#fde68a" roughness={0.55} />
      </mesh>
      {/* Medical cross */}
      <mesh position={[0, 0.06, 0.23]}>
        <boxGeometry args={[0.05, 0.14, 0.012]} />
        <meshBasicMaterial color="#ffffff" />
      </mesh>
      <mesh position={[0, 0.06, 0.23]}>
        <boxGeometry args={[0.14, 0.05, 0.012]} />
        <meshBasicMaterial color="#ffffff" />
      </mesh>
      {/* Status ring */}
      <mesh position={[0, -0.12, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[0.28, 0.32, 24]} />
        <meshBasicMaterial color={color} transparent opacity={hovered ? 0.7 : 0.3} side={THREE.DoubleSide} />
      </mesh>

      <StatusChip
        position={[0, 1.05, 0]}
        text={staff.id.replace("NURSE-", "RN").replace("DR-", "DR ")}
        color={hovered ? "#ffffff" : color}
      />

      {hovered && (
        <TooltipCard position={[0, 1.6, 0]}>
          <div style={{ fontWeight: 700, marginBottom: 2 }}>{staff.id}</div>
          <div>{isDoctor ? "Physician" : "Nurse"}</div>
          <div>Status: {staff.status === "DISPATCHED" ? "On task" : "Available"}</div>
        </TooltipCard>
      )}
    </group>
  );
}

/* ═══════════════════════════════════════════════════════════════
   Nurse Station
   ═══════════════════════════════════════════════════════════════ */
function NurseStation({ desk, deskLeg, monitor }: { desk: string; deskLeg: string; monitor: string }) {
  return (
    <group position={[0, 0, 3]}>
      {/* Desk surface */}
      <mesh position={[0, 0.75, 0]} castShadow receiveShadow>
        <boxGeometry args={[4.0, 0.07, 1.8]} />
        <meshStandardMaterial color={desk} roughness={0.35} metalness={0.45} />
      </mesh>
      {/* Legs */}
      {[[-1.8, 0, -0.8], [1.8, 0, -0.8], [-1.8, 0, 0.8], [1.8, 0, 0.8]].map((p, i) => (
        <mesh key={i} position={[p[0], 0.375, p[2]]} castShadow>
          <cylinderGeometry args={[0.04, 0.04, 0.75, 8]} />
          <meshStandardMaterial color={deskLeg} metalness={0.8} roughness={0.2} />
        </mesh>
      ))}
      {/* Monitor */}
      <mesh position={[-0.6, 1.15, 0]} castShadow>
        <boxGeometry args={[0.75, 0.45, 0.04]} />
        <meshStandardMaterial color={monitor} roughness={0.08} metalness={0.35} />
      </mesh>
      <mesh position={[-0.6, 1.15, -0.025]}>
        <boxGeometry args={[0.65, 0.38, 0.01]} />
        <meshBasicMaterial color="#22a8cc" transparent opacity={0.55} />
      </mesh>
      {/* Monitor stand */}
      <mesh position={[-0.6, 0.88, 0]} castShadow>
        <cylinderGeometry args={[0.03, 0.05, 0.18, 8]} />
        <meshStandardMaterial color={deskLeg} metalness={0.75} roughness={0.28} />
      </mesh>
      {/* Second monitor */}
      <mesh position={[0.6, 1.12, 0]} castShadow>
        <boxGeometry args={[0.6, 0.4, 0.04]} />
        <meshStandardMaterial color={monitor} roughness={0.08} metalness={0.35} />
      </mesh>
      <mesh position={[0.6, 1.12, -0.025]}>
        <boxGeometry args={[0.5, 0.33, 0.01]} />
        <meshBasicMaterial color="#10b981" transparent opacity={0.45} />
      </mesh>
      {/* Keyboard */}
      <mesh position={[0, 0.81, 0]} castShadow>
        <boxGeometry args={[0.45, 0.015, 0.18]} />
        <meshStandardMaterial color="#334155" roughness={0.5} metalness={0.25} />
      </mesh>
      {/* Chair */}
      <mesh position={[0, 0.38, 1.5]} castShadow>
        <boxGeometry args={[0.45, 0.05, 0.45]} />
        <meshStandardMaterial color="#1e40af" roughness={0.7} />
      </mesh>
      <mesh position={[0, 0.2, 1.5]} castShadow>
        <cylinderGeometry args={[0.03, 0.03, 0.36, 8]} />
        <meshStandardMaterial color={deskLeg} metalness={0.8} roughness={0.2} />
      </mesh>
      <Text position={[0, 1.7, 0]} fontSize={0.22} color="#22a8cc" anchorX="center" anchorY="bottom">
        NURSE STATION
      </Text>
    </group>
  );
}

/* ═══════════════════════════════════════════════════════════════
   Gate
   ═══════════════════════════════════════════════════════════════ */
function Gate({ position, label, glow }: { position: [number, number, number]; label: string; glow: string }) {
  const glowRef = useRef<THREE.Mesh>(null);
  useFrame(({ clock }) => {
    if (!glowRef.current) return;
    (glowRef.current.material as THREE.MeshBasicMaterial).opacity = 0.25 + Math.sin(clock.getElapsedTime() * 2) * 0.1;
  });
  return (
    <group position={position}>
      <mesh position={[0, 1.2, 0]} castShadow>
        <boxGeometry args={[2.2, 2.4, 0.2]} />
        <meshStandardMaterial color="#334155" roughness={0.55} metalness={0.4} />
      </mesh>
      <mesh ref={glowRef} position={[0, 0.9, 0.01]}>
        <boxGeometry args={[1.8, 2.0, 0.15]} />
        <meshBasicMaterial color={glow} transparent opacity={0.3} />
      </mesh>
      <mesh position={[0, 2.45, 0.12]}>
        <boxGeometry args={[2.0, 0.03, 0.03]} />
        <meshBasicMaterial color={glow} />
      </mesh>
      <Text position={[0, 2.75, 0]} fontSize={0.22} color={glow} anchorX="center" anchorY="bottom">{label}</Text>
    </group>
  );
}

/* ═══════════════════════════════════════════════════════════════
   Ward Divider (curtain rail between bed rows)
   ═══════════════════════════════════════════════════════════════ */
function WardDivider({ position, railColor }: { position: [number, number, number]; railColor: string }) {
  return (
    <group position={position}>
      {/* Rail */}
      <mesh position={[0, 2.8, 0]}>
        <boxGeometry args={[3.5, 0.03, 0.03]} />
        <meshStandardMaterial color={railColor} metalness={0.8} roughness={0.2} />
      </mesh>
      {/* Curtain panel (translucent) */}
      <mesh position={[0, 1.8, 0]}>
        <boxGeometry args={[3.2, 2.0, 0.02]} />
        <meshStandardMaterial color="#cbd5e1" roughness={0.9} transparent opacity={0.18} />
      </mesh>
    </group>
  );
}

/* ═══════════════════════════════════════════════════════════════
   Ceiling Lights
   ═══════════════════════════════════════════════════════════════ */
function CeilingLights({ fixtureColor, lightColor }: { fixtureColor: string; lightColor: string }) {
  const positions: [number, number, number][] = [
    [-8, 5.5, -4], [-4, 5.5, -4], [0, 5.5, -4], [4, 5.5, -4], [8, 5.5, -4],
    [-8, 5.5, 1],  [-4, 5.5, 1],  [0, 5.5, 1],  [4, 5.5, 1],  [8, 5.5, 1],
    [-4, 5.5, 6],  [0, 5.5, 6],   [4, 5.5, 6],
  ];
  return (
    <>
      {positions.map((pos, i) => (
        <group key={i} position={pos}>
          <mesh>
            <boxGeometry args={[0.75, 0.05, 0.28]} />
            <meshStandardMaterial color={fixtureColor} roughness={0.3} metalness={0.45} />
          </mesh>
          <mesh position={[0, -0.035, 0]}>
            <boxGeometry args={[0.65, 0.015, 0.2]} />
            <meshBasicMaterial color={lightColor} transparent opacity={0.7} />
          </mesh>
        </group>
      ))}
    </>
  );
}

/* ═══════════════════════════════════════════════════════════════
   Corridor Floor Strip
   ═══════════════════════════════════════════════════════════════ */
function CorridorStrip({ color }: { color: string }) {
  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.002, 5]} receiveShadow>
      <planeGeometry args={[FLOOR_W - 2, 3]} />
      <meshStandardMaterial color={color} roughness={0.45} metalness={0.2} />
    </mesh>
  );
}

/* ═══════════════════════════════════════════════════════════════
   In-scene HUDs
   ═══════════════════════════════════════════════════════════════ */
function hudStyle(): React.CSSProperties {
  return {
    background: "rgba(11,17,24,0.85)",
    border: "1px solid rgba(56,189,248,0.35)",
    borderRadius: 8,
    padding: "8px 12px",
    color: "#f0f6ff",
    fontFamily: "var(--font-mono), monospace",
    userSelect: "none",
  };
}

function EventTicker({ lastEvent }: { lastEvent?: string | null }) {
  const [history, setHistory] = useState<{ text: string; time: string }[]>([]);
  useEffect(() => {
    if (!lastEvent) return;
    setHistory((h) =>
      [
        { text: lastEvent, time: new Date().toLocaleTimeString([], { hour12: false }) },
        ...h,
      ].slice(0, 5)
    );
  }, [lastEvent]);

  if (!history.length) return null;
  return (
    <Html transform position={[-15.5, 4.2, -11]} scale={0.55} zIndexRange={[40, 0]} style={{ pointerEvents: "none" }}>
      <div style={{ ...hudStyle(), width: 300 }}>
        <div style={{ fontSize: 11, color: "#38bdf8", fontWeight: 700, marginBottom: 4 }}>EVENT FEED</div>
        {history.map((h, i) => (
          <div key={`${h.time}-${i}`} style={{ fontSize: 10.5, opacity: 1 - i * 0.16, lineHeight: 1.7 }}>
            <span style={{ color: "#64748b" }}>{h.time}</span> — {h.text}
          </div>
        ))}
      </div>
    </Html>
  );
}

function StepClock({ playbackInfo }: { playbackInfo?: PlaybackInfo | null }) {
  if (!playbackInfo?.active) return null;
  const step = playbackInfo.stepIndex ?? "—";
  const total = playbackInfo.totalSteps ?? "—";
  return (
    <Html transform position={[0, 5.0, -11.2]} scale={0.62} center zIndexRange={[40, 0]} style={{ pointerEvents: "none" }}>
      <div style={{ ...hudStyle(), display: "flex", gap: 14, alignItems: "baseline" }}>
        <span style={{ fontSize: 20, fontWeight: 800, color: "#38bdf8" }}>
          STEP {step}/{total}
        </span>
        <span style={{ fontSize: 13, color: "#94a3b8" }}>
          ▶ {playbackInfo.horizonType} · {playbackInfo.occupiedBeds ?? "—"}/10 beds
        </span>
      </div>
    </Html>
  );
}

/* ═══════════════════════════════════════════════════════════════
   Capacity Pressure Gauge (at nurse station)
   ═══════════════════════════════════════════════════════════════ */
function PressureGauge({ occupied, total }: { occupied: number; total: number }) {
  const ratio = occupied / Math.max(total, 1);
  const segColor =
    ratio >= 0.9 ? "#ef4444" : ratio >= 0.7 ? "#f59e0b" : "#22c55e";
  return (
    <group position={[3.4, 0, 2.2]}>
      {/* Frame */}
      <mesh position={[0, 1.05, 0]}>
        <boxGeometry args={[0.55, 2.1, 0.08]} />
        <meshStandardMaterial color="#0f172a" roughness={0.4} metalness={0.5} />
      </mesh>
      {/* Segments: bottom-up fill */}
      {Array.from({ length: total }).map((_, i) => {
        const filled = i < occupied;
        return (
          <mesh key={i} position={[0, 0.25 + i * ((1.75) / total), 0.045]}>
            <boxGeometry args={[0.4, 1.6 / total - 0.04, 0.02]} />
            <meshBasicMaterial color={filled ? segColor : "#334155"} transparent opacity={filled ? 0.95 : 0.6} />
          </mesh>
        );
      })}
      <Billboard position={[0, 2.45, 0]}>
        <Text fontSize={0.17} color={segColor} anchorX="center" outlineWidth={0.006} outlineColor="#0b1118">
          {occupied}/{total} BEDS
        </Text>
      </Billboard>
    </group>
  );
}

/* ═══════════════════════════════════════════════════════════════
   Ghost-bed preview: destination of walking patients not yet assigned
   ═══════════════════════════════════════════════════════════════ */
function GhostBeds({ patients, beds }: { patients: Patient3D[]; beds: BedState[] }) {
  const ghosts = useMemo(() => {
    const bedPos = new Map(beds.map((b) => [b.id, b.position]));
    const targets: { bedId: string; pos: Position3D }[] = [];
    for (const p of patients) {
      if (
        (p.status === "ESCORTED" || p.status === "WALKING") &&
        p.bedId &&
        !beds.find((b) => b.id === p.bedId)?.isOccupied
      ) {
        const pos = bedPos.get(p.bedId);
        if (pos && !targets.some((g) => g.bedId === p.bedId)) {
          targets.push({ bedId: p.bedId, pos });
        }
      }
    }
    return targets;
  }, [patients, beds]);

  return (
    <>
      {ghosts.map((g) => (
        <GhostBeam key={g.bedId} position={[g.pos.x, 0, g.pos.z]} label={g.bedId} />
      ))}
    </>
  );
}

function GhostBeam({ position, label }: { position: [number, number, number]; label: string }) {
  const ref = useRef<THREE.Mesh>(null);
  useFrame(({ clock }) => {
    if (!ref.current) return;
    (ref.current.material as THREE.MeshBasicMaterial).opacity =
      0.18 + Math.sin(clock.getElapsedTime() * 3) * 0.1;
  });
  return (
    <group position={position}>
      <mesh ref={ref} position={[0, 0.6, 0]}>
        <cylinderGeometry args={[0.5, 0.7, 1.4, 16, 1, true]} />
        <meshBasicMaterial color="#38bdf8" transparent opacity={0.2} side={THREE.DoubleSide} />
      </mesh>
      <Billboard position={[0, 1.6, 0]}>
        <Text fontSize={0.15} color="#38bdf8" anchorX="center" outlineWidth={0.006} outlineColor="#0b1118">
          ➜ {label}
        </Text>
      </Billboard>
    </group>
  );
}

/* ═══════════════════════════════════════════════════════════════
   Camera focus rig: smoothly moves OrbitControls target to focus point
   ═══════════════════════════════════════════════════════════════ */
function CameraRig({ focus }: { focus: [number, number, number] | null }) {
  const controls = useThree((s) => s.controls) as unknown as
    | { target: THREE.Vector3; update: () => void }
    | undefined;
  const targetRef = useRef(new THREE.Vector3(0, 0.5, 0));

  useFrame(() => {
    if (!controls || !focus) return;
    targetRef.current.lerp(new THREE.Vector3(focus[0], focus[1], focus[2]), 0.06);
    controls.target.copy(targetRef.current);
    controls.update();
  });
  return null;
}

/* ═══════════════════════════════════════════════════════════════
   Nurse Station triage monitor: live top-3 fast-track queue
   ═══════════════════════════════════════════════════════════════ */
const TRIAGE_STATUS_COLORS: Record<string, string> = {
  READY_TO_ASSIGN: "#22c55e",
  AWAITING_EVS_CLEANING: "#f59e0b",
  NEEDS_EXPEDITED_DISCHARGE: "#f97316",
};

function TriageMonitor({ matches }: { matches: FastTrackMatch[] }) {
  const top = matches.slice(0, 3);
  return (
    <Html transform position={[0.6, 1.15, 3]} rotation={[0, -0.5, 0]} scale={0.24} zIndexRange={[30, 0]} style={{ pointerEvents: "none" }}>
      <div
        style={{
          width: 300,
          background: "rgba(6,12,20,0.94)",
          border: "1px solid rgba(16,185,129,0.5)",
          borderRadius: 6,
          padding: "10px 12px",
          color: "#d1fae5",
          fontFamily: "var(--font-mono), monospace",
          boxShadow: "0 0 24px rgba(16,185,129,0.25)",
        }}
      >
        <div style={{ fontSize: 11, fontWeight: 700, color: "#10b981", letterSpacing: "0.08em", marginBottom: 6 }}>
          ⚡ FAST-TRACK TRIAGE · LIVE
        </div>
        {top.length === 0 ? (
          <div style={{ fontSize: 11, color: "#475569" }}>NO ACTIVE SURGE</div>
        ) : (
          top.map((m, i) => {
            const c = TRIAGE_STATUS_COLORS[m.allocation_status] ?? "#38bdf8";
            return (
              <div key={i} style={{ display: "flex", gap: 8, alignItems: "baseline", fontSize: 11, lineHeight: 2 }}>
                <span style={{ color: "#475569" }}>{i + 1}.</span>
                <span
                  style={{
                    fontWeight: 800,
                    color: "#fff",
                    background: m.esi_level <= 2 ? "#ef4444" : m.esi_level === 3 ? "#f59e0b" : "#22c55e",
                    borderRadius: 3,
                    padding: "0 4px",
                  }}
                >
                  ESI{m.esi_level}
                </span>
                <span style={{ color: "#e2e8f0" }}>{m.mrn}</span>
                <span style={{ color: "#38bdf8", fontWeight: 700 }}>{m.matched_bed_id ?? "—"}</span>
                <span style={{ marginLeft: "auto", color: c, fontWeight: 700, fontSize: 9 }}>
                  {m.allocation_status.replace(/_/g, " ")}
                </span>
              </div>
            );
          })
        )}
      </div>
    </Html>
  );
}

/* ═══════════════════════════════════════════════════════════════
   Scene
   ═══════════════════════════════════════════════════════════════ */
function Scene({
  beds,
  patients,
  staff,
  theme,
  playbackInfo,
  lastEvent,
  fastTrackMatches = [],
  focusBedId = null,
}: HospitalFloorProps) {
  const t = THEME[theme];
  const halfW = FLOOR_W / 2;
  const halfH = FLOOR_H / 2;
  const [focus, setFocus] = useState<[number, number, number] | null>(null);
  const [focusedLabel, setFocusedLabel] = useState<string | null>(null);

  // External focus requests (e.g. bed-assignments table row click)
  useEffect(() => {
    if (!focusBedId) return;
    const bed = beds.find((b) => b.id === focusBedId.bedId);
    if (bed) {
      setFocus([bed.position.x, 0.5, bed.position.z]);
      setFocusedLabel(bed.id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusBedId]);

  // ESC resets camera focus
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setFocus(null); setFocusedLabel(null); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const handleSelect = useCallback(
    (entity: { kind: string; id: string; bedId?: string }) => {
      let pos: [number, number, number] | null = null;
      let label = entity.id;
      if (entity.kind === "bed") {
        const b = beds.find((x) => x.id === entity.id);
        if (b) pos = [b.position.x, 0.5, b.position.z];
      } else if (entity.kind === "patient") {
        const p = patients.find((x) => x.id === entity.id);
        if (p) {
          const targetBed = entity.bedId ? beds.find((x) => x.id === entity.bedId) : undefined;
          pos = targetBed ? [targetBed.position.x, 0.5, targetBed.position.z] : [p.position.x, 0.75, p.position.z];
          label = `${entity.id}${entity.bedId ? ` → ${entity.bedId}` : ""}`;
        }
      } else {
        const s = staff.find((x) => x.id === entity.id);
        if (s) pos = [s.position.x, 0.75, s.position.z];
      }
      if (pos) { setFocus(pos); setFocusedLabel(label); }
    },
    [beds, patients, staff]
  );

  const occupiedCount = beds.filter((b) => b.isOccupied).length;

  return (
    <>
      <fog attach="fog" args={[t.clear, t.fogNear, t.fogFar]} />

      <ambientLight intensity={t.ambientIntensity} color={t.ambientColor} />
      <directionalLight
        position={[10, 20, 14]}
        intensity={t.sunIntensity}
        castShadow
        shadow-mapSize-width={2048}
        shadow-mapSize-height={2048}
        shadow-camera-left={-halfW - 2}
        shadow-camera-right={halfW + 2}
        shadow-camera-top={halfH + 2}
        shadow-camera-bottom={-halfH - 2}
        shadow-bias={-0.001}
      />
      <directionalLight position={[-10, 12, -8]} intensity={0.4} color="#93c5fd" />
      <pointLight position={[0, 6, 10]} intensity={0.45} color="#fde68a" distance={20} />
      <pointLight position={[-10, 4, -5]} intensity={0.3} color="#818cf8" distance={16} />
      <pointLight position={[10, 3, 4]} intensity={0.25} color="#34d399" distance={14} />
      <pointLight position={[-10, 3, 4]} intensity={0.25} color="#60a5fa" distance={14} />

      <OrbitControls makeDefault maxPolarAngle={Math.PI / 2.1} minDistance={5} maxDistance={35} enableDamping dampingFactor={0.05} />

      {/* Floor */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.01, 0]} receiveShadow>
        <planeGeometry args={[FLOOR_W, FLOOR_H]} />
        <meshStandardMaterial color={t.floor} roughness={0.55} metalness={0.18} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.005, 0]} receiveShadow>
        <planeGeometry args={[FLOOR_W, FLOOR_H]} />
        <meshStandardMaterial color={t.floorReflect} roughness={0.2} metalness={0.4} transparent opacity={0.2} />
      </mesh>

      <CorridorStrip color={t.corridor} />
      <Grid args={[FLOOR_W, FLOOR_H]} position={[0, 0.01, 0]} sectionSize={1} sectionColor={t.gridSection} cellColor={t.gridCell} fadeDistance={40} />

      {/* Walls */}
      <mesh position={[-halfW, 2.5, 0]} castShadow receiveShadow>
        <boxGeometry args={[0.2, 5, FLOOR_H]} />
        <meshStandardMaterial color={t.wall} roughness={0.82} />
      </mesh>
      <mesh position={[halfW, 2.5, 0]} castShadow receiveShadow>
        <boxGeometry args={[0.2, 5, FLOOR_H]} />
        <meshStandardMaterial color={t.wall} roughness={0.82} />
      </mesh>
      <mesh position={[0, 2.5, -halfH]} castShadow receiveShadow>
        <boxGeometry args={[FLOOR_W, 5, 0.2]} />
        <meshStandardMaterial color={t.wall} roughness={0.82} />
      </mesh>

      <CeilingLights fixtureColor={t.ceilingFixture} lightColor={t.ceilingLight} />
      <DustParticles color={t.dust} />

      {/* Plants */}
      {PLANT_POSITIONS.map((pos, i) => (
        <Plant key={i} position={pos} potColor={t.plantPot} leafColor={t.plantLeaf} />
      ))}

      {/* Ward dividers between bed rows */}
      {[-7.5, -2.5, 2.5, 7.5].map((x, i) => (
        <WardDivider key={i} position={[x, 0, -2.5]} railColor={t.curtainRail} />
      ))}

      <NurseStation desk={t.desk} deskLeg={t.deskLeg} monitor={t.monitorFrame} />
      <TriageMonitor matches={fastTrackMatches} />
      <PressureGauge occupied={occupiedCount} total={beds.length || 10} />
      <Gate position={[-13, 0, 8]} label="ADMISSION" glow="#60a5fa" />
      <Gate position={[13, 0, 8]} label="DISCHARGE" glow="#34d399" />

      {beds.map((bed) => (
        <BedMesh
          key={bed.id}
          bed={bed}
          labelColor={t.label}
          ivColor={t.ivStand}
          onSelect={handleSelect}
          fastTrackMatches={fastTrackMatches}
        />
      ))}
      {patients.map((p) => (
        <PatientAvatar key={p.id} patient={p} onSelect={handleSelect} />
      ))}
      {staff.map((s) => (
        <StaffAvatar key={s.id} staff={s} onSelect={handleSelect} />
      ))}

      <GhostBeds patients={patients} beds={beds} />
      <EventTicker lastEvent={lastEvent} />
      <StepClock playbackInfo={playbackInfo} />
      <CameraRig focus={focus} />

      {/* Focus indicator + reset hint */}
      {focusedLabel && (
        <Html transform position={[0, 3.4, -11.2]} scale={0.5} center zIndexRange={[40, 0]} style={{ pointerEvents: "none" }}>
          <div style={{ ...hudStyle(), fontSize: 12 }}>
            🎯 FOCUSED: <strong>{focusedLabel}</strong>
            <span style={{ color: "#64748b" }}> — press ESC to reset view</span>
          </div>
        </Html>
      )}
    </>
  );
}

/* ═══════════════════════════════════════════════════════════════
   Export
   ═══════════════════════════════════════════════════════════════ */
export default function HospitalFloor({
  beds,
  patients,
  staff,
  theme,
  playbackInfo = null,
  lastEvent = null,
  fastTrackMatches = [],
  focusBedId = null,
}: HospitalFloorProps) {
  const t = THEME[theme];
  return (
    <div style={{ width: "100%", height: "100%", background: t.clear }}>
      <Canvas
        camera={{ position: [0, 20, 24], fov: 48 }}
        shadows
        gl={{ antialias: true, toneMapping: THREE.ACESFilmicToneMapping, toneMappingExposure: theme === "dark" ? 1.15 : 1.35 }}
        onCreated={({ gl }) => gl.setClearColor(t.clear)}
      >
        <Suspense fallback={null}>
          <Scene
            beds={beds}
            patients={patients}
            staff={staff}
            theme={theme}
            playbackInfo={playbackInfo}
            lastEvent={lastEvent}
            fastTrackMatches={fastTrackMatches}
            focusBedId={focusBedId}
          />
          <EffectComposer>
            <Bloom intensity={0.55} luminanceThreshold={0.55} luminanceSmoothing={0.3} mipmapBlur />
          </EffectComposer>
        </Suspense>
      </Canvas>
    </div>
  );
}
