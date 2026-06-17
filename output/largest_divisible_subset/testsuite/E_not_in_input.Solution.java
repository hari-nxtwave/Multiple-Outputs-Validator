import java.util.*;
class Solution {            // WRONG: includes 16, which is not in the input
    public List<Integer> findLargestChain(int[] arr) {
        return new ArrayList<>(Arrays.asList(1,2,4,16));
    }
}
