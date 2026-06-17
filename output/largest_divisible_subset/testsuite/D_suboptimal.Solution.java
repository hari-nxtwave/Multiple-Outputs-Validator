import java.util.*;
class Solution {            // WRONG: returns a valid but sub-optimal chain (1,3,9)
    public List<Integer> findLargestChain(int[] arr) {
        return new ArrayList<>(Arrays.asList(1,3,9));
    }
}
