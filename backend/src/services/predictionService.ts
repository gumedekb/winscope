import { spawn } from 'child_process';
import path from 'path';

export const predictionService = {
  predict: async (features: any): Promise<any> => {
    return new Promise((resolve, reject) => {
      const pythonPath = path.join(__dirname, '../../venv/bin/python');
      const scriptPath = path.join(__dirname, '../../model/predict.py');
      
      const pyProg = spawn(pythonPath, [scriptPath]);
      
      let output = '';
      let error = '';

      pyProg.stdin.write(JSON.stringify(features));
      pyProg.stdin.end();

      pyProg.stdout.on('data', (data) => {
        output += data.toString();
      });

      pyProg.stderr.on('data', (data) => {
        error += data.toString();
      });

      pyProg.on('close', (code) => {
        if (code !== 0) {
          reject(new Error(`Python process exited with code ${code}: ${error}`));
          return;
        }
        try {
          const result = JSON.parse(output);
          if (result.error) {
            reject(new Error(result.error));
          } else {
            resolve(result);
          }
        } catch (e) {
          reject(new Error(`Failed to parse python output: ${output}`));
        }
      });
    });
  }
};
