import java.util.logging.*;

public class TestApp {
    private static final Logger logger = Logger.getLogger(TestApp.class.getName());

    public static void main(String[] args) {
        try {
            simulateError();
        } catch (Exception e) {
            logger.log(Level.SEVERE, "Exception occurred", e);
        }
    }

    private static void simulateError() {
        String test = null;
        // This will throw NullPointerException
        test.toString();
    }
}
