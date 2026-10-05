declare module "d3-force-3d" {
  interface AxisForce {
    strength(value: number): AxisForce;
  }
  export function forceX(x?: number): AxisForce;
  export function forceY(y?: number): AxisForce;
  export function forceZ(z?: number): AxisForce;
}
