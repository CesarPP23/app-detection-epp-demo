import { beforeEach, describe, expect, it } from "vitest";
import {
  classColor,
  classLabel,
  isFixedClass,
  OTHER_COLOR,
  resetDynamicClasses,
} from "./classColors";

describe("classColors", () => {
  beforeEach(resetDynamicClasses);

  it("asigna un color fijo por nombre de clase (token CSS)", () => {
    expect(classColor("hairnet")).toBe("var(--class-hairnet)");
    expect(classColor("lab_coat")).toBe("var(--class-lab_coat)");
    expect(classColor("face_mask")).toBe("var(--class-face_mask)");
    expect(classColor("latex_gloves")).toBe("var(--class-latex_gloves)");
  });

  it("el color no depende del orden de consulta ni del ranking", () => {
    const a = [classColor("latex_gloves"), classColor("hairnet")];
    const b = [classColor("hairnet"), classColor("latex_gloves")].reverse();
    expect(a).toEqual(b);
  });

  it("clases nuevas: slots 5-8 estables y luego 'Otras' en gris", () => {
    const first = classColor("boots");
    expect(classColor("boots")).toBe(first);
    const others = ["c2", "c3", "c4"].map(classColor);
    expect(new Set([first, ...others]).size).toBe(4);
    expect(classColor("c5")).toBe(OTHER_COLOR);
    expect(classColor("boots")).toBe(first); // no se repinta al aparecer más clases
  });

  it("etiquetas en español", () => {
    expect(classLabel("latex_gloves")).toBe("Guantes de látex");
    expect(classLabel("safety_boots")).toBe("safety boots");
    expect(classLabel("Cofia")).toBe("Cofia");
    expect(classLabel("episodio_no_conforme")).toBe("No cumple EPP");
    expect(isFixedClass("hairnet")).toBe(true);
    expect(isFixedClass("boots")).toBe(false);
  });
});
