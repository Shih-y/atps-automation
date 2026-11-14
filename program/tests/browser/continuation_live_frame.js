// Real HTTP, injected fake controller, synthetic camera; inherits isolated fixture cleanup.
process.env.TEST_CONTINUATION_LOOP = '1';
require('./experiment_live_frame.js');
