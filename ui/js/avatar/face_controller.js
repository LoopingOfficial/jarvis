/* JARVIS AvatarFaceController — contrat unique pour l'animation faciale. */
import * as THREE from '../../vendor/three.module.js';

const clamp = THREE.MathUtils.clamp;

export class AvatarFaceController {
  constructor(model) {
    this.model = model;
    this.blink = { left: 0, right: 0 };
    this.expression = { smile: 0, frown: 0, browUp: 0, browDown: 0, browInnerUp: 0 };
  }

  _set(names, value) {
    for (const name of names) this.model.setMorph(name, clamp(value, 0, 1));
  }

  setBlink(left = 0, right = left) {
    this.blink.left = clamp(left, 0, 1);
    this.blink.right = clamp(right, 0, 1);
    this._set(['blink_L', 'blinkLeft'], this.blink.left);
    this._set(['blink_R', 'blinkRight'], this.blink.right);
  }

  setExpression(values = {}) {
    Object.assign(this.expression, values);
    this._set(['smile', 'smileLeft', 'smileRight'], this.expression.smile);
    this._set(['frown', 'mouthFrown'], this.expression.frown);
    this._set(['brow_up', 'browUp'], this.expression.browUp);
    this._set(['brow_down', 'browDown'], this.expression.browDown);
    this._set(['brow_inner_up', 'browUpLeft', 'browUpRight'], this.expression.browInnerUp);
  }

  setMouth(name, value = 0) {
    this._set([name, name.startsWith('viseme_') ? name : `viseme_${name}`], value);
  }

  clearMouth() {
    for (const name of this.model.morphNames()) {
      if (name.startsWith('viseme_') || /mouth|jaw/i.test(name)) this.model.setMorph(name, 0);
    }
  }
}

window.AvatarFaceController = AvatarFaceController;
export default AvatarFaceController;
